"""Durable, append-only storage for AcademyInfo API response payloads.

The storage layer deliberately knows nothing about HTTP clients.  A coordinator
hands it response bytes and metadata; it atomically publishes the raw bytes and
then appends one JSON object to the collection manifest.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

try:  # pragma: no cover - Windows fallback keeps the module importable.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class StorageError(RuntimeError):
    """Base class for raw-response storage failures."""


class TaskConflictError(StorageError):
    """A task ID already exists with a different response checksum."""


class RawPathConflictError(StorageError):
    """A requested raw path already exists and must not be overwritten."""


class ManifestIntegrityError(StorageError):
    """The manifest or a raw artifact referenced by it is inconsistent."""


class UnsafeParameterError(ValueError):
    """A parameter would lose identifier fidelity or cannot be serialized."""


class DownloadIntegrityError(StorageError):
    """A temporary download does not match its downloader-supplied facts."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_endpoint_url(value: str) -> None:
    endpoint = urlsplit(value)
    if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
        raise ValueError("endpoint_url must be an absolute HTTP(S) URL")
    if endpoint.username is not None or endpoint.password is not None:
        raise ValueError("endpoint_url must not contain user information")
    if endpoint.query or endpoint.fragment:
        raise ValueError("endpoint_url must not contain a query or fragment")


def _validate_shape_metadata(
    *,
    column_count: int,
    field_names: Any,
    duplicate_row_count: int,
    row_count: int,
) -> tuple[str, ...]:
    for name, value in (
        ("column_count", column_count),
        ("duplicate_row_count", duplicate_row_count),
        ("row_count", row_count),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
    if isinstance(field_names, (str, bytes)):
        raise ValueError("field_names must be a tuple or list of strings")
    try:
        names = tuple(field_names)
    except TypeError as error:
        raise ValueError("field_names must be a tuple or list of strings") from error
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("field_names must contain only non-empty strings")
    if len(set(names)) != len(names):
        raise ValueError("field_names must not contain duplicates")
    if names != tuple(sorted(names)):
        raise ValueError("field_names must be sorted")
    if column_count != len(names):
        raise ValueError("column_count must equal the number of unique field_names")
    if duplicate_row_count < 0 or duplicate_row_count > row_count:
        raise ValueError("duplicate_row_count must be between 0 and row_count")
    return names


@dataclass(frozen=True)
class CollectionMetadata:
    """Non-payload facts supplied by the collection coordinator."""

    task_id: str
    data_id: str
    service: str
    operation: str
    endpoint_url: str
    scope_strategy: str
    params: Mapping[str, Any]
    http_status: int
    result_code: str | None
    total_count: int | None
    row_count: int
    column_count: int
    field_names: tuple[str, ...]
    duplicate_row_count: int
    collected_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        for field_name in (
            "data_id",
            "service",
            "operation",
            "endpoint_url",
            "scope_strategy",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        _validate_endpoint_url(self.endpoint_url)
        if self.http_status < 100 or self.http_status > 599:
            raise ValueError("http_status must be between 100 and 599")
        if self.total_count is not None and self.total_count < 0:
            raise ValueError("total_count must be non-negative or None")
        if self.row_count < 0:
            raise ValueError("row_count must be non-negative")
        names = _validate_shape_metadata(
            column_count=self.column_count,
            field_names=self.field_names,
            duplicate_row_count=self.duplicate_row_count,
            row_count=self.row_count,
        )
        object.__setattr__(self, "field_names", names)
        if not self.collected_at.strip():
            raise ValueError("collected_at must not be empty")


@dataclass(frozen=True)
class ManifestRecord:
    """One durable JSONL manifest record."""

    task_id: str
    data_id: str
    service: str
    operation: str
    endpoint_url: str
    scope_strategy: str
    raw_path: str
    sha256: str
    bytes: int
    collected_at: str
    http_status: int
    result_code: str | None
    total_count: int | None
    row_count: int
    column_count: int
    field_names: tuple[str, ...]
    duplicate_row_count: int
    params: dict[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "task_id",
            "data_id",
            "service",
            "operation",
            "endpoint_url",
            "scope_strategy",
            "raw_path",
            "collected_at",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        _validate_endpoint_url(self.endpoint_url)
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 hex digest")
        if self.bytes < 0 or self.row_count < 0:
            raise ValueError("bytes and row_count must be non-negative")
        if self.total_count is not None and self.total_count < 0:
            raise ValueError("total_count must be non-negative or None")
        if self.http_status < 100 or self.http_status > 599:
            raise ValueError("http_status must be between 100 and 599")
        names = _validate_shape_metadata(
            column_count=self.column_count,
            field_names=self.field_names,
            duplicate_row_count=self.duplicate_row_count,
            row_count=self.row_count,
        )
        object.__setattr__(self, "field_names", names)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManifestRecord":
        try:
            return cls(
                task_id=str(value["task_id"]),
                data_id=str(value["data_id"]),
                service=str(value["service"]),
                operation=str(value["operation"]),
                endpoint_url=str(value["endpoint_url"]),
                scope_strategy=str(value["scope_strategy"]),
                raw_path=str(value["raw_path"]),
                sha256=str(value["sha256"]),
                bytes=int(value["bytes"]),
                collected_at=str(value["collected_at"]),
                http_status=int(value["http_status"]),
                result_code=(
                    None if value.get("result_code") is None else str(value["result_code"])
                ),
                total_count=(
                    None if value.get("total_count") is None else int(value["total_count"])
                ),
                row_count=int(value["row_count"]),
                column_count=int(value["column_count"]),
                field_names=tuple(value["field_names"]),
                duplicate_row_count=int(value["duplicate_row_count"]),
                params=dict(value["params"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ManifestIntegrityError(f"Invalid manifest record: {error}") from error


@dataclass(frozen=True)
class SaveResult:
    """Outcome of publishing a response or recognizing an exact resume hit."""

    status: str
    record: ManifestRecord

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


_SECRET_PARAM_KEYS = {
    "apikey",
    "authorization",
    "authkey",
    "servicekey",
}
_IDENTIFIER_PARAM_KEYS = {
    "campuscode",
    "campusid",
    "departmentcode",
    "departmentid",
    "majorcode",
    "majorid",
    "schldivcd",
    "schlid",
    "schoolcode",
    "schoolid",
}


def _normalized_key(key: str) -> str:
    return "".join(character for character in key.casefold() if character.isalnum())


def safe_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON-safe parameters with credentials removed.

    Known school/campus/department identifiers must arrive as strings.  This
    makes accidental integer conversion (and loss of leading zeroes) fail fast.
    """

    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if not isinstance(key, str):
            raise UnsafeParameterError("parameter names must be strings")
        normalized = _normalized_key(key)
        if normalized in _SECRET_PARAM_KEYS:
            continue
        if normalized in _IDENTIFIER_PARAM_KEYS and value is not None and not isinstance(value, str):
            raise UnsafeParameterError(f"{key} must be a string so leading zeroes are preserved")
        try:
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise UnsafeParameterError(f"{key} is not JSON serializable: {error}") from error
        cleaned[key] = value
    return cleaned


class AcademyInfoStorage:
    """Publish immutable raw responses and maintain an append-only manifest.

    Paths passed to :meth:`save_bytes` are repository-relative and must remain
    below ``raw_root``.  The default layout therefore records paths such as
    ``data/raw/academyinfo/task.xml`` in the manifest.
    """

    def __init__(
        self,
        repository_root: Path,
        *,
        raw_root: Path | str = Path("data/raw"),
        manifest_path: Path | str = Path("data/metadata/academyinfo_manifest.jsonl"),
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.raw_root = self._resolve_repository_path(raw_root)
        self.manifest_path = self._resolve_repository_path(manifest_path)
        if self.manifest_path == self.raw_root or self.raw_root in self.manifest_path.parents:
            raise ValueError("manifest_path must be outside raw_root")
        self._thread_lock = threading.RLock()
        self._manifest_identity: tuple[int, int] | None = None
        self._manifest_offset = 0
        self._records_cache: list[ManifestRecord] = []
        self._records_by_task: dict[str, list[ManifestRecord]] = {}

    def _resolve_repository_path(self, path: Path | str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        resolved = candidate.resolve(strict=False)
        if resolved != self.repository_root and self.repository_root not in resolved.parents:
            raise ValueError(f"path escapes repository_root: {path}")
        return resolved

    def _raw_destination(self, raw_path: Path | str) -> tuple[Path, str]:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            destination = candidate.resolve(strict=False)
        else:
            destination = (self.repository_root / candidate).resolve(strict=False)
        if destination == self.raw_root or self.raw_root not in destination.parents:
            raise ValueError(f"raw_path must be a file below {self.raw_root}: {raw_path}")
        relative = destination.relative_to(self.repository_root)
        return destination, PurePosixPath(relative).as_posix()

    @contextmanager
    def _locked_manifest(self) -> Iterator[Any]:
        with self._thread_lock:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with self.manifest_path.open("a+b") as stream:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    self._refresh_index(stream)
                    yield stream
                finally:
                    if fcntl is not None:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _add_cached_record(self, record: ManifestRecord) -> None:
        self._records_cache.append(record)
        self._records_by_task.setdefault(record.task_id, []).append(record)

    def _refresh_index(self, stream: Any) -> None:
        stat = os.fstat(stream.fileno())
        identity = (stat.st_dev, stat.st_ino)
        if self._manifest_identity is None:
            self._manifest_identity = identity
        elif identity != self._manifest_identity:
            raise ManifestIntegrityError(
                f"manifest file was replaced while storage was active: {self.manifest_path}"
            )
        if stat.st_size < self._manifest_offset:
            raise ManifestIntegrityError(
                f"manifest file was truncated while storage was active: {self.manifest_path}"
            )

        stream.seek(self._manifest_offset)
        line_number = len(self._records_cache)
        pending_records: list[ManifestRecord] = []
        while True:
            line_start = stream.tell()
            raw_line = stream.readline()
            if not raw_line:
                break
            line_number += 1
            if not raw_line.endswith(b"\n"):
                stream.seek(line_start)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                break
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
                if not isinstance(value, dict):
                    raise TypeError("record is not an object")
                pending_records.append(ManifestRecord.from_dict(value))
            except (json.JSONDecodeError, TypeError, ManifestIntegrityError) as error:
                raise ManifestIntegrityError(
                    f"Invalid manifest line {line_number} in {self.manifest_path}: {error}"
                ) from error
        for record in pending_records:
            self._add_cached_record(record)
        self._manifest_offset = stream.tell()

    def _append_record(self, manifest: Any, record: ManifestRecord) -> None:
        line = (
            json.dumps(
                asdict(record),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        manifest.seek(0, os.SEEK_END)
        manifest.write(line)
        manifest.flush()
        os.fsync(manifest.fileno())
        self._add_cached_record(record)
        self._manifest_offset = manifest.tell()

    def records(self) -> list[ManifestRecord]:
        """Read and validate all existing manifest records."""

        with self._locked_manifest() as stream:
            return list(self._records_cache)

    def _validate_record_artifact(self, record: ManifestRecord) -> None:
        try:
            raw_file, normalized_raw_path = self._raw_destination(record.raw_path)
        except ValueError as error:
            raise ManifestIntegrityError(
                f"manifest raw_path is invalid for task_id {record.task_id!r}: "
                f"{record.raw_path}"
            ) from error
        if normalized_raw_path != record.raw_path:
            raise ManifestIntegrityError(
                f"manifest raw_path is not normalized for task_id {record.task_id!r}"
            )
        if not raw_file.is_file():
            raise ManifestIntegrityError(
                f"manifest target is missing for task_id {record.task_id!r}: "
                f"{record.raw_path}"
            )
        actual_checksum, actual_bytes = self._file_facts(raw_file)
        if actual_checksum != record.sha256 or actual_bytes != record.bytes:
            raise ManifestIntegrityError(
                f"manifest target integrity mismatch for task_id {record.task_id!r}"
            )

    def _completed_record(
        self,
        task_id: str,
    ) -> ManifestRecord | None:
        matches = self._records_by_task.get(task_id, [])
        if not matches:
            return None
        checksums = {record.sha256 for record in matches}
        if len(checksums) != 1:
            raise TaskConflictError(
                f"task_id {task_id!r} has multiple checksums: {sorted(checksums)}"
            )
        for record in matches:
            self._validate_record_artifact(record)
        return matches[0]

    def completed_record(self, task_id: str) -> ManifestRecord | None:
        """Return a verified completed task before making a network request.

        ``None`` means the task has not been recorded.  Multiple checksums for
        the same task are a conflict; missing or modified raw files are manifest
        integrity failures.
        """

        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        with self._locked_manifest() as stream:
            return self._completed_record(task_id)

    def _resume_record(
        self,
        task_id: str,
        checksum: str,
    ) -> ManifestRecord | None:
        record = self._completed_record(task_id)
        if record is not None and record.sha256 != checksum:
            raise TaskConflictError(
                f"task_id {task_id!r} already has a different checksum: {record.sha256}"
            )
        return record

    def resume_record(self, task_id: str, checksum: str) -> ManifestRecord | None:
        """Return an exact completed task, or raise on a task-ID conflict."""

        if not task_id.strip():
            raise ValueError("task_id must not be empty")
        if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
            raise ValueError("checksum must be a lowercase SHA-256 hex digest")
        with self._locked_manifest() as stream:
            return self._resume_record(task_id, checksum)

    def save_bytes(
        self,
        payload: bytes,
        raw_path: Path | str,
        metadata: CollectionMetadata,
    ) -> SaveResult:
        """Atomically publish in-memory *payload* without replacing a file."""

        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        descriptor, temporary_name = tempfile.mkstemp(prefix="academyinfo-storage-")
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            return self.save_file(
                temporary_path,
                raw_path,
                metadata,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_bytes=len(payload),
            )
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _file_facts(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_count = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                byte_count += len(chunk)
        return digest.hexdigest(), byte_count

    @staticmethod
    def _copy_to_local_temporary(source: Path, destination: Path) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".partial",
        )
        local_temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                with source.open("rb") as input_stream:
                    shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
        except Exception:
            local_temporary.unlink(missing_ok=True)
            raise
        return local_temporary

    def save_file(
        self,
        temporary_path: Path,
        raw_path: Path | str,
        metadata: CollectionMetadata,
        *,
        expected_sha256: str | None = None,
        expected_bytes: int | None = None,
    ) -> SaveResult:
        """Validate and consume a downloaded temporary file.

        On ``stored`` or ``skipped`` the temporary file is removed.  On any
        conflict or integrity failure it is retained for caller-side diagnosis.
        ``expected_sha256`` and ``expected_bytes`` allow the downloader's
        streaming calculations to be checked before publication.
        """

        source = Path(temporary_path)
        if not source.is_file():
            raise FileNotFoundError(f"temporary download does not exist: {source}")
        destination, manifest_raw_path = self._raw_destination(raw_path)
        if source.resolve() == destination:
            raise ValueError("temporary_path and raw_path must be different files")
        checksum, byte_count = self._file_facts(source)
        if expected_sha256 is not None and checksum != expected_sha256:
            raise DownloadIntegrityError(
                f"temporary download SHA-256 mismatch: expected {expected_sha256}, got {checksum}"
            )
        if expected_bytes is not None and byte_count != expected_bytes:
            raise DownloadIntegrityError(
                f"temporary download byte count mismatch: expected {expected_bytes}, got {byte_count}"
            )
        with source.open("rb") as stream:
            os.fsync(stream.fileno())
        params = safe_params(metadata.params)
        record = ManifestRecord(
            task_id=metadata.task_id,
            data_id=metadata.data_id,
            service=metadata.service,
            operation=metadata.operation,
            endpoint_url=metadata.endpoint_url,
            scope_strategy=metadata.scope_strategy,
            raw_path=manifest_raw_path,
            sha256=checksum,
            bytes=byte_count,
            collected_at=metadata.collected_at,
            http_status=metadata.http_status,
            result_code=metadata.result_code,
            total_count=metadata.total_count,
            row_count=metadata.row_count,
            column_count=metadata.column_count,
            field_names=metadata.field_names,
            duplicate_row_count=metadata.duplicate_row_count,
            params=params,
        )

        with self._locked_manifest() as manifest:
            completed = self._resume_record(metadata.task_id, checksum)
            if completed is not None:
                source.unlink()
                return SaveResult(status="skipped", record=completed)
            if destination.exists():
                if not destination.is_file():
                    raise RawPathConflictError(
                        f"raw path exists and is not a file: {manifest_raw_path}"
                    )
                destination_checksum, destination_bytes = self._file_facts(destination)
                if (destination_checksum, destination_bytes) != (checksum, byte_count):
                    raise RawPathConflictError(
                        f"raw path already exists with different content: "
                        f"{manifest_raw_path}"
                    )
                self._append_record(manifest, record)
                source.unlink()
                return SaveResult(status="stored", record=record)

            destination.parent.mkdir(parents=True, exist_ok=True)
            local_temporary: Path | None = None
            published = False
            try:
                try:
                    os.link(source, destination)
                    published = True
                except FileExistsError as error:
                    raise RawPathConflictError(
                        f"raw path was created concurrently: {manifest_raw_path}"
                    ) from error
                except OSError as error:
                    if error.errno != errno.EXDEV:
                        raise
                    local_temporary = self._copy_to_local_temporary(source, destination)
                    try:
                        os.link(local_temporary, destination)
                        published = True
                    except FileExistsError as conflict:
                        raise RawPathConflictError(
                            f"raw path was created concurrently: {manifest_raw_path}"
                        ) from conflict

                self._append_record(manifest, record)
            except Exception:
                if published:
                    try:
                        destination.unlink()
                    except FileNotFoundError:
                        pass
                raise
            finally:
                if local_temporary is not None:
                    local_temporary.unlink(missing_ok=True)

        source.unlink()
        return SaveResult(status="stored", record=record)


__all__ = [
    "AcademyInfoStorage",
    "CollectionMetadata",
    "DownloadIntegrityError",
    "ManifestIntegrityError",
    "ManifestRecord",
    "RawPathConflictError",
    "SaveResult",
    "StorageError",
    "TaskConflictError",
    "UnsafeParameterError",
    "safe_params",
]
