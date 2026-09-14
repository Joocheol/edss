"""Reusable AcademyInfo live-validation workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .client import (
    AcademyInfoClient,
    ProbeDefaults,
    load_service_key,
    load_yaml,
    representative_params,
)


YEAR_OMISSION_OPERATIONS = (
    "getComparisonEnrolledStudentCrntSt",
    "getUniversityCode",
    "getComparisonFullTimeFacultyForPersonStudentNumberEnrolledStudent",
    "getComparisonTuitionCrntSt",
    "getSchoolMajorInfo",
    "getComparisonDormitoryAcceptanceCrntSt",
    "getUniversityMajorCode",
    "getCsptDsgnOperCstt",
    "getSchoolInfo",
)

MULTI_SCHOOL_OPERATIONS = (
    "getComparisonEnrolledStudentCrntSt",
    "getComparisonFullTimeFacultyForPersonStudentNumberEnrolledStudent",
    "getComparisonTuitionCrntSt",
    "getComparisonDormitoryAcceptanceCrntSt",
    "getUniversityMajorCode",
    "getCsptDsgnOperCstt",
)


class ValidationError(RuntimeError):
    """Raised when a live response violates a validated AcademyInfo rule."""


@dataclass(frozen=True)
class ValidationContext:
    """Validated configuration and client needed by the workflow."""

    client: AcademyInfoClient
    endpoints: tuple[Mapping[str, Any], ...]
    default_concurrency: int
    max_concurrency: int


@dataclass(frozen=True)
class ValidationRun:
    """Structured outputs from one complete live-validation run."""

    started_at_utc: str
    finished_at_utc: str
    probe_results: list[dict[str, Any]]
    service_summary: list[dict[str, Any]]
    year_omission_results: list[dict[str, Any]]
    school_scope_results: list[dict[str, Any]]
    school_info_total_count: int
    school_major_total_count: int
    university_code_total_count: int
    page_boundary_duplicates: int
    concurrency_results: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        """Return the bounded, JSON-serializable final result."""

        return {
            "run_started_at_utc": self.started_at_utc,
            "run_finished_at_utc": self.finished_at_utc,
            "services": len({row["service"] for row in self.probe_results}),
            "operations": len(self.probe_results),
            "operations_passed": sum(
                row["error"] is None and row["result_code"] == "00"
                for row in self.probe_results
            ),
            "operations_with_rows": sum(
                row["total_count"] > 0 for row in self.probe_results
            ),
            "operations_with_normal_zero_rows": sum(
                row["total_count"] == 0 for row in self.probe_results
            ),
            "year_omission_zero": (
                f"{sum(row['total_count'] == 0 for row in self.year_omission_results)}/"
                f"{len(self.year_omission_results)}"
            ),
            "school_omission_zero": (
                f"{sum(row['omitted_total'] == 0 for row in self.school_scope_results)}/"
                f"{len(self.school_scope_results)}"
            ),
            "multi_school_comma_pipe_unsupported": (
                f"{sum(row['comma_total'] == 0 and row['pipe_total'] == 0 for row in self.school_scope_results)}/"
                f"{len(self.school_scope_results)}"
            ),
            "repeated_school_parameter_uses_first": (
                f"{sum(row['repeated_matches_first'] for row in self.school_scope_results)}/"
                f"{len(self.school_scope_results)}"
            ),
            "school_major_total_count": self.school_major_total_count,
            "school_major_page_boundary_duplicates": self.page_boundary_duplicates,
            "concurrency_4_passed": (
                f"{sum(row['error'] is None and row['result_code'] == '00' for row in self.concurrency_results)}/"
                f"{len(self.concurrency_results)}"
            ),
            "created_data_files": 0,
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def load_validation_context(
    env_path: Path,
    endpoint_config_path: Path,
    runtime_config_path: Path,
) -> ValidationContext:
    """Load inputs, enforce the fixed 9-service/103-operation contract, and build a client."""

    endpoint_config = load_yaml(endpoint_config_path)
    runtime_config = load_yaml(runtime_config_path)
    endpoints = endpoint_config.get("endpoints")
    runtime = runtime_config.get("academyinfo")

    _require(
        endpoint_config.get("scope") == {"service_count": 9, "operation_count": 103},
        "Endpoint scope must be exactly 9 services and 103 operations",
    )
    _require(isinstance(endpoints, list), "Endpoint config must contain an endpoints list")
    _require(len(endpoints) == 103, "Endpoint config must contain exactly 103 operations")
    _require(
        len({item["service"] for item in endpoints}) == 9,
        "Endpoint config must contain exactly 9 services",
    )
    _require(
        len({(item["service"], item["operation"]) for item in endpoints}) == 103,
        "Endpoint service/operation keys must be unique",
    )
    _require(isinstance(runtime, dict), "Runtime config must contain academyinfo settings")
    default_concurrency = runtime.get("default_concurrency")
    max_concurrency = runtime.get("max_concurrency")
    _require(default_concurrency == 2, "Default concurrency must be 2")
    _require(max_concurrency == 4, "Maximum verified concurrency must be 4")

    client = AcademyInfoClient(
        load_service_key(env_path),
        timeout_seconds=int(runtime["request_timeout_seconds"]),
    )
    return ValidationContext(
        client=client,
        endpoints=tuple(endpoints),
        default_concurrency=default_concurrency,
        max_concurrency=max_concurrency,
    )


def _operation_index(
    endpoints: Sequence[Mapping[str, Any]],
    operations: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for operation in operations:
        matches = [endpoint for endpoint in endpoints if endpoint["operation"] == operation]
        _require(len(matches) == 1, f"Expected one endpoint for {operation}, found {len(matches)}")
        index[operation] = matches[0]
    return index


def _summarize_services(probe_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for service in sorted({str(row["service"]) for row in probe_results}):
        rows = [row for row in probe_results if row["service"] == service]
        summaries.append(
            {
                "service": service,
                "operations": len(rows),
                "with_rows": sum(row["total_count"] > 0 for row in rows),
                "zero_rows": sum(row["total_count"] == 0 for row in rows),
                "median_ms": sorted(row["elapsed_ms"] for row in rows)[len(rows) // 2],
            }
        )
    return summaries


def run_validation(
    context: ValidationContext,
    *,
    defaults: ProbeDefaults | None = None,
    progress: Callable[[str], None] | None = None,
) -> ValidationRun:
    """Execute the complete 103-operation live probe and request-semantics checks."""

    defaults = defaults or ProbeDefaults()
    emit = progress or (lambda _message: None)
    client = context.client
    endpoints = context.endpoints
    started_at_utc = datetime.now(timezone.utc).isoformat()

    probe_results = client.probe_endpoints(
        endpoints,
        defaults,
        max_workers=context.default_concurrency,
    )
    probe_failures = [row for row in probe_results if row["error"] is not None]
    non_normal = [row for row in probe_results if row.get("result_code") != "00"]
    invalid_counts = [
        row
        for row in probe_results
        if row.get("total_count", -1) < 0 or row.get("row_count", 0) > 1
    ]
    _require(not probe_failures, f"Transport failures: {len(probe_failures)}")
    _require(not non_normal, f"Non-normal result codes: {len(non_normal)}")
    _require(not invalid_counts, f"Invalid bounded row counts: {len(invalid_counts)}")
    _require(len(probe_results) == 103, f"Expected 103 probes, found {len(probe_results)}")
    emit("103개 기능: HTTP/XML/resultCode/totalCount 검증 통과")

    operation_index = _operation_index(
        endpoints,
        tuple(dict.fromkeys((*YEAR_OMISSION_OPERATIONS, *MULTI_SCHOOL_OPERATIONS))),
    )
    year_omission_results = []
    for operation in YEAR_OMISSION_OPERATIONS:
        endpoint = operation_index[operation]
        params = representative_params(endpoint, defaults)
        params.pop("svyYr", None)
        result = client.request_xml(str(endpoint["endpoint_url"]), params)
        year_omission_results.append(
            {
                "operation": operation,
                "result_code": result["result_code"],
                "total_count": result["total_count"],
            }
        )
    _require(
        all(
            row["result_code"] == "00" and row["total_count"] == 0
            for row in year_omission_results
        ),
        "Year omission must return a normal zero count for all 9 representative operations",
    )

    school_scope_results = []
    for operation in MULTI_SCHOOL_OPERATIONS:
        endpoint = operation_index[operation]
        endpoint_url = str(endpoint["endpoint_url"])
        base_params = representative_params(endpoint, defaults)
        single = client.request_xml(endpoint_url, base_params)

        omitted_params = dict(base_params)
        omitted_params.pop("schlId")
        omitted = client.request_xml(endpoint_url, omitted_params)
        comma = client.request_xml(
            endpoint_url,
            {
                **base_params,
                "schlId": f"{defaults.school_id},{defaults.comparison_school_id}",
            },
        )
        pipe_result = client.request_xml(
            endpoint_url,
            {
                **base_params,
                "schlId": f"{defaults.school_id}|{defaults.comparison_school_id}",
            },
        )
        repeated = client.request_xml(
            endpoint_url,
            base_params,
            repeated_school_ids=[defaults.school_id, defaults.comparison_school_id],
        )
        school_scope_results.append(
            {
                "operation": operation,
                "single_total": single["total_count"],
                "omitted_total": omitted["total_count"],
                "comma_total": comma["total_count"],
                "pipe_total": pipe_result["total_count"],
                "repeated_total": repeated["total_count"],
                "repeated_school_ids": repeated["school_ids"],
                "repeated_matches_first": repeated["rows"] == single["rows"],
            }
        )
    _require(
        all(row["omitted_total"] == 0 for row in school_scope_results),
        "School omission must return zero rows for all 6 school-scoped operations",
    )
    _require(
        all(
            row["comma_total"] == 0 and row["pipe_total"] == 0
            for row in school_scope_results
        ),
        "Comma and pipe multi-school forms must be unsupported for all 6 operations",
    )
    _require(
        all(row["repeated_matches_first"] for row in school_scope_results),
        "Repeated school parameters must match the first school response",
    )
    _require(
        all(row["repeated_school_ids"] == [defaults.school_id] for row in school_scope_results),
        "Repeated school parameters must return only the first school",
    )
    emit("연도·학교 생략과 다중 학교코드 규칙 검증 통과")

    probe_by_operation = {row["operation"]: row for row in probe_results}
    school_info_total_count = probe_by_operation["getSchoolInfo"]["total_count"]
    school_major_total_count = probe_by_operation["getSchoolMajorInfo"]["total_count"]
    university_code_total_count = probe_by_operation["getUniversityCode"]["total_count"]
    _require(school_info_total_count > 1, "SchoolInfo nationwide probe must return multiple rows")
    _require(school_major_total_count > 1, "SchoolMajor nationwide probe must return multiple rows")
    _require(university_code_total_count > 1, "UniversityCode probe must return multiple rows")

    school_major_endpoint = operation_index["getSchoolMajorInfo"]
    page_1 = client.request_xml(
        str(school_major_endpoint["endpoint_url"]),
        representative_params(school_major_endpoint, defaults, page_no=1, num_rows=500),
    )
    page_2 = client.request_xml(
        str(school_major_endpoint["endpoint_url"]),
        representative_params(school_major_endpoint, defaults, page_no=2, num_rows=500),
    )
    _require(
        page_1["result_code"] == page_2["result_code"] == "00",
        "Both SchoolMajor boundary pages must return resultCode 00",
    )
    _require(
        page_1["total_count"] == page_2["total_count"],
        "SchoolMajor boundary pages must report the same totalCount",
    )
    _require(
        page_1["row_count"] == page_2["row_count"] == 500,
        "Both SchoolMajor boundary pages must contain 500 rows",
    )
    page_boundary_duplicates = len(set(page_1["rows"]) & set(page_2["rows"]))

    concurrency_results = client.probe_endpoints(
        endpoints[:8],
        defaults,
        max_workers=context.max_concurrency,
    )
    _require(len(concurrency_results) == 8, "Concurrency sample must contain 8 operations")
    _require(
        all(row["error"] is None and row["result_code"] == "00" for row in concurrency_results),
        "All concurrency-4 sample operations must pass",
    )
    emit("학과정보 페이지 경계와 동시성 4 검증 통과")

    result = ValidationRun(
        started_at_utc=started_at_utc,
        finished_at_utc=datetime.now(timezone.utc).isoformat(),
        probe_results=probe_results,
        service_summary=_summarize_services(probe_results),
        year_omission_results=year_omission_results,
        school_scope_results=school_scope_results,
        school_info_total_count=school_info_total_count,
        school_major_total_count=school_major_total_count,
        university_code_total_count=university_code_total_count,
        page_boundary_duplicates=page_boundary_duplicates,
        concurrency_results=concurrency_results,
    )
    summary = result.summary()
    _require(summary["services"] == 9, "Validation must cover 9 services")
    _require(
        summary["operations"] == summary["operations_passed"] == 103,
        "All 103 operations must pass",
    )
    return result
