"""End-to-end AcademyInfo raw-data collection orchestration.

The collector expands the reviewed endpoint configuration into immutable tasks,
discovers the valid school universe for each survey year, and persists complete
XML responses through the streaming downloader and append-only storage layer.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .downloader import (
    AcademyInfoDownloadError,
    AcademyInfoDownloader,
    TemporaryXmlDownload,
    XmlResponseMetadata,
)
from .planning import CollectionTask, build_collection_tasks
from .storage import AcademyInfoStorage, CollectionMetadata, ManifestRecord


UNIVERSITY_CODE_OPERATION = "getUniversityCode"
DEFAULT_SCHOOL_DIVISION_CODES = ("01", "02")
_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9_-]+$")


class CollectionError(RuntimeError):
    """Raised when a response violates the reviewed collection contract."""


class VerifiedRowLimitError(CollectionError):
    """Raised to stop a run that exceeds the reviewed single-response limit."""


class TaskExecutionError(CollectionError):
    """A safe, expected per-task failure with its attempted request count."""

    def __init__(self, error_type: str, message: str, *, request_count: int) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.request_count = request_count


@dataclass(frozen=True)
class CollectionPolicy:
    """Runtime limits for a resumable collection run."""

    concurrency: int = 2
    max_concurrency: int = 4
    probe_page_size: int = 1
    max_verified_rows: int = 70_000

    def __post_init__(self) -> None:
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if self.concurrency > self.max_concurrency:
            raise ValueError(
                f"concurrency {self.concurrency} exceeds reviewed maximum "
                f"{self.max_concurrency}"
            )
        if self.probe_page_size < 1:
            raise ValueError("probe_page_size must be at least 1")
        if self.max_verified_rows < 1:
            raise ValueError("max_verified_rows must be at least 1")

    @classmethod
    def from_runtime_config(
        cls,
        runtime_config: Mapping[str, Any],
        *,
        concurrency: int | None = None,
    ) -> "CollectionPolicy":
        academyinfo = runtime_config.get("academyinfo")
        if not isinstance(academyinfo, Mapping):
            raise CollectionError("runtime config is missing academyinfo mapping")
        single_page = academyinfo.get("single_page")
        if not isinstance(single_page, Mapping):
            raise CollectionError("runtime config is missing academyinfo.single_page")
        return cls(
            concurrency=(
                int(academyinfo["default_concurrency"])
                if concurrency is None
                else concurrency
            ),
            max_concurrency=int(academyinfo["max_concurrency"]),
            probe_page_size=int(academyinfo["probe_page_size"]),
            max_verified_rows=int(single_page["max_verified_rows"]),
        )


@dataclass(frozen=True)
class TaskResult:
    """One completed or resumed collection task."""

    task: CollectionTask
    status: str
    record: ManifestRecord
    request_count: int


@dataclass(frozen=True)
class FailedTask:
    """A secret-free failure summary suitable for terminal reporting."""

    task_id: str
    service: str
    operation: str
    error_type: str
    error: str
    request_count: int


@dataclass(frozen=True)
class SchoolDiscovery:
    """Verified yearly school universes and their bootstrap task results."""

    schools_by_year: dict[str, tuple[str, ...]]
    results: tuple[TaskResult, ...]


@dataclass(frozen=True)
class CollectionRun:
    """Results of a bounded collection execution."""

    planned: int
    results: tuple[TaskResult, ...]
    failures: tuple[FailedTask, ...]

    def summary(self) -> dict[str, Any]:
        statuses = Counter(result.status for result in self.results)
        return {
            "planned": self.planned,
            "completed": len(self.results),
            "stored": statuses["stored"],
            "skipped": statuses["skipped"],
            "failed": len(self.failures),
            "http_requests": (
                sum(result.request_count for result in self.results)
                + sum(failure.request_count for failure in self.failures)
            ),
        }


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def school_ids_from_xml(
    path: Path,
    *,
    expected_year: str | None = None,
    expected_row_count: int | None = None,
) -> list[str]:
    """Read and validate one school ID per item without coercing identifiers."""

    school_ids: list[str] = []
    for _event, element in ET.iterparse(path, events=("end",)):
        if _local_name(element.tag) != "item":
            continue
        id_values = [
            (child.text or "").strip()
            for child in list(element)
            if _local_name(child.tag) == "schlId" and (child.text or "").strip()
        ]
        if len(id_values) != 1:
            raise CollectionError(
                f"school-code item must contain exactly one non-empty schlId: {path}"
            )
        if expected_year is not None:
            year_values = [
                (child.text or "").strip()
                for child in list(element)
                if _local_name(child.tag) == "svyYr" and (child.text or "").strip()
            ]
            if year_values != [expected_year]:
                raise CollectionError(
                    f"school-code item survey year does not match {expected_year}: {path}"
                )
        school_ids.append(id_values[0])
        element.clear()
    if not school_ids:
        raise CollectionError(f"school-code response contains no school IDs: {path}")
    if expected_row_count is not None and len(school_ids) != expected_row_count:
        raise CollectionError(
            f"school-code item count {len(school_ids)} does not match recorded row count "
            f"{expected_row_count}: {path}"
        )
    unique_ids = set(school_ids)
    if len(unique_ids) != len(school_ids):
        raise CollectionError(f"school-code response contains duplicate schlId values: {path}")
    return sorted(unique_ids)


def select_endpoint_config(
    endpoint_config: Mapping[str, Any],
    operations: Iterable[str] | None,
) -> dict[str, Any]:
    """Return a shallow config containing only explicitly selected operations."""

    endpoints = endpoint_config.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        raise CollectionError("endpoint config contains no endpoints")
    if operations is None:
        return {**endpoint_config, "endpoints": list(endpoints)}

    requested = set(operations)
    if not requested or any(not isinstance(value, str) or not value for value in requested):
        raise CollectionError("operations must contain non-empty strings")
    available = {
        endpoint.get("operation")
        for endpoint in endpoints
        if isinstance(endpoint, Mapping)
    }
    unknown = sorted(requested - available)
    if unknown:
        raise CollectionError(f"unknown operation(s): {', '.join(unknown)}")
    selected = [
        endpoint
        for endpoint in endpoints
        if isinstance(endpoint, Mapping) and endpoint.get("operation") in requested
    ]
    return {**endpoint_config, "endpoints": selected}


def _endpoint_for_operation(
    endpoint_config: Mapping[str, Any], operation: str
) -> Mapping[str, Any]:
    matches = [
        endpoint
        for endpoint in endpoint_config.get("endpoints", [])
        if isinstance(endpoint, Mapping) and endpoint.get("operation") == operation
    ]
    if len(matches) != 1:
        raise CollectionError(
            f"expected exactly one configured {operation} endpoint; found {len(matches)}"
        )
    return matches[0]


def _raw_path(task: CollectionTask) -> Path:
    for value, label in ((task.service, "service"), (task.operation, "operation")):
        if not _SAFE_PATH_PART.fullmatch(value):
            raise CollectionError(f"unsafe {label} path component: {value!r}")
    return Path(
        "data",
        "raw",
        "academyinfo",
        task.service,
        task.operation,
        f"{task.task_id}.xml",
    )


class AcademyInfoCollector:
    """Coordinate planning, bounded downloads, validation, and durable storage."""

    def __init__(
        self,
        *,
        endpoint_config: Mapping[str, Any],
        downloader: AcademyInfoDownloader | None,
        storage: AcademyInfoStorage,
        policy: CollectionPolicy,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.endpoint_config = endpoint_config
        self.downloader = downloader
        self.storage = storage
        self.policy = policy
        self.progress = progress
        endpoints = endpoint_config.get("endpoints")
        if not isinstance(endpoints, list):
            raise CollectionError("endpoint config contains no endpoints list")
        self._expected_fields: dict[tuple[str, str], tuple[str, ...]] = {}
        for endpoint in endpoints:
            if not isinstance(endpoint, Mapping):
                raise CollectionError("endpoint config entries must be mappings")
            service = endpoint.get("service")
            operation = endpoint.get("operation")
            output_fields = endpoint.get("output_fields")
            if not isinstance(service, str) or not isinstance(operation, str):
                raise CollectionError("endpoint service and operation must be strings")
            if (
                not isinstance(output_fields, list)
                or not output_fields
                or any(not isinstance(name, str) or not name for name in output_fields)
                or len(set(output_fields)) != len(output_fields)
            ):
                raise CollectionError(
                    f"{service}/{operation}: output_fields must be unique strings"
                )
            self._expected_fields[(service, operation)] = tuple(sorted(output_fields))

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    @staticmethod
    def _validate_response(
        metadata: XmlResponseMetadata,
        *,
        task: CollectionTask,
        expected_items: int,
        expected_total: int | None = None,
    ) -> None:
        label = f"{task.service}/{task.operation} ({task.task_id})"
        if metadata.http_status != 200:
            raise CollectionError(f"{label}: unexpected HTTP {metadata.http_status}")
        if metadata.result_code != "00":
            raise CollectionError(
                f"{label}: API resultCode is {metadata.result_code!r}, expected '00'"
            )
        if metadata.total_count is None:
            raise CollectionError(f"{label}: response has no numeric totalCount")
        if expected_total is not None and metadata.total_count != expected_total:
            raise CollectionError(
                f"{label}: totalCount changed from {expected_total} "
                f"to {metadata.total_count}"
            )
        if metadata.item_count != expected_items:
            raise CollectionError(
                f"{label}: received {metadata.item_count} item(s), "
                f"expected {expected_items}"
            )

    def _validate_fields(
        self,
        metadata: XmlResponseMetadata,
        *,
        task: CollectionTask,
    ) -> None:
        expected = self._expected_fields[(task.service, task.operation)]
        actual = metadata.field_names
        if metadata.item_count == 0:
            if actual:
                raise CollectionError(
                    f"{task.service}/{task.operation} ({task.task_id}): zero-row "
                    "response unexpectedly contains item fields"
                )
            return
        unexpected = sorted(set(actual) - set(expected))
        if unexpected:
            raise CollectionError(
                f"{task.service}/{task.operation} ({task.task_id}): response "
                f"contains fields not declared in config; unexpected={unexpected}"
            )

    def collect_task(self, task: CollectionTask) -> TaskResult:
        """Collect one task using the reviewed probe-then-single-page policy."""

        completed = self.storage.completed_record(task.task_id)
        if completed is not None:
            return TaskResult(task=task, status="skipped", record=completed, request_count=0)
        if self.downloader is None:
            raise CollectionError("collection requires an AcademyInfo downloader")

        request_count = 0
        probe: TemporaryXmlDownload | None = None
        final: TemporaryXmlDownload | None = None
        base_params = task.request_params()
        probe_params = {
            **base_params,
            "pageNo": "1",
            "numOfRows": str(self.policy.probe_page_size),
        }
        try:
            probe = self.downloader.download_to_temporary_file(
                task.endpoint_url, probe_params
            )
            request_count += probe.metadata.attempts
            total_count = probe.metadata.total_count
            if total_count is None:
                self._validate_response(
                    probe.metadata, task=task, expected_items=probe.metadata.item_count
                )
                raise AssertionError("unreachable")
            self._validate_response(
                probe.metadata,
                task=task,
                expected_items=min(total_count, self.policy.probe_page_size),
            )
            if total_count > self.policy.max_verified_rows:
                raise VerifiedRowLimitError(
                    f"{task.service}/{task.operation} ({task.task_id}): totalCount "
                    f"{total_count} exceeds reviewed single-response limit "
                    f"{self.policy.max_verified_rows}"
                )

            final_params = probe_params
            final = probe
            if total_count > self.policy.probe_page_size:
                probe.path.unlink(missing_ok=True)
                probe = None
                final_params = {
                    **base_params,
                    "pageNo": "1",
                    "numOfRows": str(total_count),
                }
                final = self.downloader.download_to_temporary_file(
                    task.endpoint_url, final_params
                )
                request_count += final.metadata.attempts
                self._validate_response(
                    final.metadata,
                    task=task,
                    expected_items=total_count,
                    expected_total=total_count,
                )

            assert final is not None
            self._validate_fields(final.metadata, task=task)
            metadata = CollectionMetadata(
                task_id=task.task_id,
                data_id=task.data_id,
                service=task.service,
                operation=task.operation,
                endpoint_url=task.endpoint_url,
                scope_strategy=task.scope_strategy,
                params=final_params,
                http_status=final.metadata.http_status,
                result_code=final.metadata.result_code,
                total_count=final.metadata.total_count,
                row_count=final.metadata.item_count,
                column_count=len(final.metadata.field_names),
                field_names=final.metadata.field_names,
                duplicate_row_count=final.metadata.duplicate_row_count,
            )
            saved = self.storage.save_file(
                final.path,
                _raw_path(task),
                metadata,
                expected_sha256=final.metadata.sha256,
                expected_bytes=final.metadata.bytes_written,
            )
            final = None
            return TaskResult(
                task=task,
                status=saved.status,
                record=saved.record,
                request_count=request_count,
            )
        except VerifiedRowLimitError:
            raise
        except AcademyInfoDownloadError as error:
            raise TaskExecutionError(
                type(error).__name__,
                str(error),
                request_count=request_count + error.attempts,
            ) from None
        except CollectionError as error:
            raise TaskExecutionError(
                type(error).__name__, str(error), request_count=request_count
            ) from None
        finally:
            if probe is not None:
                probe.path.unlink(missing_ok=True)
            if final is not None:
                final.path.unlink(missing_ok=True)

    def discover_schools(
        self,
        years: Sequence[str],
        *,
        allow_download: bool,
    ) -> SchoolDiscovery:
        """Load verified school universes, optionally collecting missing years."""

        endpoint = _endpoint_for_operation(self.endpoint_config, UNIVERSITY_CODE_OPERATION)
        bootstrap_config = {**self.endpoint_config, "endpoints": [endpoint]}
        tasks = build_collection_tasks(bootstrap_config, years=years)
        schools_by_year: dict[str, tuple[str, ...]] = {}
        results: list[TaskResult] = []
        for task in tasks:
            year = task.request_params()["svyYr"]
            if not allow_download and not self.storage.manifest_path.exists():
                raise CollectionError(
                    f"school universe for {year} is not cached; provide --school-id "
                    "or explicitly allow school discovery"
                )
            completed = self.storage.completed_record(task.task_id)
            if completed is None and not allow_download:
                raise CollectionError(
                    f"school universe for {year} is not cached; provide --school-id "
                    "or explicitly allow school discovery"
                )
            result = self.collect_task(task)
            raw_file = self.storage.repository_root / result.record.raw_path
            schools_by_year[year] = tuple(
                school_ids_from_xml(
                    raw_file,
                    expected_year=year,
                    expected_row_count=result.record.row_count,
                )
            )
            results.append(result)
            self._report(
                f"school universe {year}: {len(schools_by_year[year])} schools "
                f"({result.status})"
            )
        return SchoolDiscovery(
            schools_by_year=schools_by_year,
            results=tuple(results),
        )

    def plan(
        self,
        *,
        years: Sequence[str],
        operations: Iterable[str] | None = None,
        school_ids: Sequence[str] | None = None,
        schools_by_year: Mapping[str, Sequence[str]] | None = None,
        school_division_codes: Sequence[str] = DEFAULT_SCHOOL_DIVISION_CODES,
    ) -> list[CollectionTask]:
        """Create an exact, side-effect-free task list."""

        selected_config = select_endpoint_config(self.endpoint_config, operations)
        needs_schools = any(
            endpoint.get("scope_strategy") == "per_school_by_year"
            for endpoint in selected_config["endpoints"]
        )
        resolved_schools: Mapping[str, Sequence[str]] | None = schools_by_year
        if needs_schools:
            if school_ids is not None and schools_by_year is not None:
                raise CollectionError("provide school_ids or schools_by_year, not both")
            if school_ids is not None:
                resolved_schools = {year: school_ids for year in years}
            elif resolved_schools is None:
                raise CollectionError(
                    "per-school operations require school_ids or schools_by_year"
                )
        return build_collection_tasks(
            selected_config,
            years=years,
            schools_by_year=resolved_schools,
            school_division_codes=school_division_codes,
        )

    def run(
        self,
        tasks: Sequence[CollectionTask],
    ) -> CollectionRun:
        """Execute all tasks, continuing after secret-free per-task failures."""

        results: list[TaskResult] = []
        failures: list[FailedTask] = []
        total = len(tasks)
        executor = ThreadPoolExecutor(max_workers=self.policy.concurrency)
        task_iterator = iter(tasks)
        futures: dict[Future[TaskResult], CollectionTask] = {}

        def submit_next() -> bool:
            try:
                task = next(task_iterator)
            except StopIteration:
                return False
            futures[executor.submit(self.collect_task, task)] = task
            return True

        for _ in range(min(total, self.policy.concurrency * 2)):
            submit_next()
        try:
            finished = 0
            while futures:
                completed, _pending = wait(futures, return_when=FIRST_COMPLETED)
                for future in completed:
                    task = futures.pop(future)
                    finished += 1
                    try:
                        result = future.result()
                        results.append(result)
                        self._report(
                            f"[{finished}/{total}] {result.status} "
                            f"{task.service}/{task.operation}"
                        )
                    except TaskExecutionError as error:
                        failures.append(
                            FailedTask(
                                task_id=task.task_id,
                                service=task.service,
                                operation=task.operation,
                                error_type=error.error_type,
                                error=str(error),
                                request_count=error.request_count,
                            )
                        )
                        self._report(
                            f"[{finished}/{total}] failed "
                            f"{task.service}/{task.operation}: "
                            f"{error.error_type}: {error}"
                        )
                    submit_next()
        except BaseException:
            for future in futures:
                future.cancel()
            # Running workers cannot be cancelled safely. Wait for their finally
            # blocks and any atomic save already in progress before returning a
            # fatal error, so the caller never observes post-return mutations.
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
        return CollectionRun(
            planned=total,
            results=tuple(sorted(results, key=lambda value: value.task.task_id)),
            failures=tuple(sorted(failures, key=lambda value: value.task_id)),
        )


__all__ = [
    "AcademyInfoCollector",
    "CollectionError",
    "CollectionPolicy",
    "CollectionRun",
    "DEFAULT_SCHOOL_DIVISION_CODES",
    "FailedTask",
    "SchoolDiscovery",
    "TaskExecutionError",
    "TaskResult",
    "VerifiedRowLimitError",
    "school_ids_from_xml",
    "select_endpoint_config",
]
