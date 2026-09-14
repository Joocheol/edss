"""Streaming download primitives for the AcademyInfo Open API.

This module deliberately knows nothing about collection plans, final raw-data
paths, or manifests.  It downloads and inspects exactly one XML response while
keeping the service key out of caller-controlled parameters and diagnostics.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
_RESULT_CODE_NAMES = frozenset({"resultCode", "returnReasonCode"})
_RESULT_MESSAGE_NAMES = frozenset(
    {"resultMsg", "resultMessage", "returnAuthMsg", "errMsg"}
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class XmlResponseMetadata:
    """Secret-free facts computed while downloading one XML response."""

    http_status: int
    bytes_written: int
    sha256: str
    result_code: str | None
    result_message: str | None
    total_count: int | None
    item_count: int
    elapsed_ms: int
    attempts: int
    field_names: tuple[str, ...] = ()
    duplicate_row_count: int = 0


@dataclass(frozen=True)
class TemporaryXmlDownload:
    """A downloaded temporary file; the caller is responsible for deleting it."""

    path: Path
    metadata: XmlResponseMetadata


class AcademyInfoDownloadError(RuntimeError):
    """A deliberately URL-free and service-key-free download failure."""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.http_status = http_status


class AcademyInfoLocalIOError(RuntimeError):
    """A fatal, secret-free failure while handling a local temporary file."""

    def __init__(self, operation: str, error: OSError) -> None:
        errno_summary = "unknown" if error.errno is None else str(error.errno)
        super().__init__(
            f"AcademyInfo local I/O failed during {operation} (errno {errno_summary})"
        )
        self.operation = operation
        self.errno = error.errno


def _raise_local_io(operation: str, error: OSError) -> None:
    raise AcademyInfoLocalIOError(operation, error) from None


@dataclass(frozen=True)
class _XmlResponseProfile:
    result_code: str | None
    result_message: str | None
    total_count: int | None
    item_count: int
    field_names: tuple[str, ...]
    duplicate_row_count: int


def _update_canonical_digest(digest: Any, value: str) -> None:
    encoded = value.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, byteorder="big"))
    digest.update(encoded)


def _parse_xml_profile(path: Path) -> _XmlResponseProfile:
    """Profile one XML response while retaining only per-row SHA-256 digests."""

    result_code: str | None = None
    result_message: str | None = None
    total_text: str | None = None
    item_count = 0
    field_names: set[str] = set()
    row_digests: set[bytes] = set()
    duplicate_row_count = 0
    element_stack: list[str] = []
    item_depth: int | None = None
    row_digest: Any = None

    try:
        for event, element in ET.iterparse(path, events=("start", "end")):
            name = _local_name(element.tag)
            if event == "start":
                element_stack.append(name)
                if name == "item" and item_depth is None:
                    item_depth = len(element_stack)
                    row_digest = hashlib.sha256()
                continue

            text = (element.text or "").strip()
            if result_code is None and name in _RESULT_CODE_NAMES:
                result_code = text or None
            elif result_message is None and name in _RESULT_MESSAGE_NAMES:
                result_message = text or None
            elif total_text is None and name == "totalCount":
                total_text = text or None

            depth = len(element_stack)
            if item_depth is not None and depth == item_depth + 1:
                field_names.add(name)
                _update_canonical_digest(row_digest, name)
                _update_canonical_digest(row_digest, text)
            elif item_depth is not None and depth == item_depth and name == "item":
                item_count += 1
                canonical_digest = row_digest.digest()
                if canonical_digest in row_digests:
                    duplicate_row_count += 1
                else:
                    row_digests.add(canonical_digest)
                item_depth = None
                row_digest = None

            element.clear()
            element_stack.pop()
    except OSError as error:
        _raise_local_io("XML response read", error)

    try:
        total_count = int(total_text) if total_text is not None else None
    except ValueError:
        total_count = None
    return _XmlResponseProfile(
        result_code=result_code,
        result_message=result_message,
        total_count=total_count,
        item_count=item_count,
        field_names=tuple(sorted(field_names)),
        duplicate_row_count=duplicate_row_count,
    )


def parse_xml_metadata(path: Path) -> tuple[str | None, str | None, int | None, int]:
    """Return the original four-field metadata tuple for public compatibility."""

    profile = _parse_xml_profile(path)
    return (
        profile.result_code,
        profile.result_message,
        profile.total_count,
        profile.item_count,
    )


class AcademyInfoDownloader:
    """Download AcademyInfo XML responses with bounded retries and disk streaming."""

    def __init__(
        self,
        service_key: str,
        *,
        timeout_seconds: float = 60.0,
        attempts: int = 3,
        backoff_seconds: float = 0.5,
        user_agent: str = "edss-academyinfo-collector/1.0",
        chunk_size: int = 1024 * 1024,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not service_key:
            raise ValueError("service_key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must not be negative")
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")

        self._service_key = service_key
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.user_agent = user_agent
        self.chunk_size = chunk_size
        self._sleep = sleep

    def _build_url(self, endpoint_url: str, params: Mapping[str, object]) -> str:
        """Build the request URL internally, discarding caller-supplied keys."""

        parts = urlsplit(endpoint_url)
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() != "servicekey"
        ]
        query.extend(
            (str(key), str(value))
            for key, value in params.items()
            if key.lower() != "servicekey" and value is not None
        )
        query.insert(0, ("serviceKey", self._service_key))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )

    def _request_once(
        self,
        endpoint_url: str,
        params: Mapping[str, object],
        path: Path,
    ) -> tuple[int, int, str]:
        request = Request(
            self._build_url(endpoint_url, params),
            headers={"User-Agent": self.user_agent, "Accept": "application/xml"},
        )
        byte_count = 0
        digest = hashlib.sha256()
        with urlopen(request, timeout=self.timeout_seconds) as response:
            status_value = getattr(response, "status", None)
            status = int(response.getcode() if status_value is None else status_value)
            try:
                output = path.open("wb")
            except OSError as error:
                _raise_local_io("temporary response file open", error)
            try:
                while True:
                    chunk = response.read(self.chunk_size)
                    if not chunk:
                        break
                    try:
                        output.write(chunk)
                    except OSError as error:
                        _raise_local_io("temporary response file write", error)
                    digest.update(chunk)
                    byte_count += len(chunk)
                try:
                    output.flush()
                    os.fsync(output.fileno())
                except OSError as error:
                    _raise_local_io("temporary response file sync", error)
            finally:
                try:
                    output.close()
                except OSError as error:
                    _raise_local_io("temporary response file close", error)
        return status, byte_count, digest.hexdigest()

    def download_to_temporary_file(
        self,
        endpoint_url: str,
        params: Mapping[str, object],
        *,
        directory: Path | None = None,
    ) -> TemporaryXmlDownload:
        """Stream one response to a new temporary path and inspect its XML.

        The returned path remains on disk until the caller deletes it.  Failed
        attempts are removed inside this method.
        """

        started = time.perf_counter()
        last_summary = "transport error"
        last_status: int | None = None

        for attempt_number in range(1, self.attempts + 1):
            try:
                descriptor, raw_path = tempfile.mkstemp(
                    prefix="academyinfo-", suffix=".xml", dir=directory
                )
            except OSError as error:
                _raise_local_io("temporary response file creation", error)
            path = Path(raw_path)
            try:
                os.close(descriptor)
            except OSError as error:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                _raise_local_io("temporary response file creation", error)
            retryable = True
            succeeded = False
            try:
                status, byte_count, sha256 = self._request_once(
                    endpoint_url, params, path
                )
                profile = _parse_xml_profile(path)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                succeeded = True
                return TemporaryXmlDownload(
                    path=path,
                    metadata=XmlResponseMetadata(
                        http_status=status,
                        bytes_written=byte_count,
                        sha256=sha256,
                        result_code=profile.result_code,
                        result_message=profile.result_message,
                        total_count=profile.total_count,
                        item_count=profile.item_count,
                        elapsed_ms=elapsed_ms,
                        attempts=attempt_number,
                        field_names=profile.field_names,
                        duplicate_row_count=profile.duplicate_row_count,
                    ),
                )
            except HTTPError as error:
                last_status = error.code
                last_summary = f"HTTP {error.code}"
                retryable = error.code in _RETRYABLE_HTTP_STATUSES
                error.close()
            except (URLError, TimeoutError):
                last_summary = "transport error"
            except OSError:
                last_summary = "transport error"
            except (ET.ParseError, ValueError):
                last_summary = "invalid XML response"
            finally:
                if not succeeded:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        _raise_local_io("temporary response file cleanup", error)

            if not retryable or attempt_number == self.attempts:
                raise AcademyInfoDownloadError(
                    f"AcademyInfo download failed after {attempt_number} attempt(s): "
                    f"{last_summary}",
                    attempts=attempt_number,
                    http_status=last_status,
                ) from None
            self._sleep(self.backoff_seconds * (2 ** (attempt_number - 1)))

        raise AssertionError("unreachable")

    def download_to_file(
        self,
        endpoint_url: str,
        params: Mapping[str, object],
        destination: BinaryIO,
        *,
        temporary_directory: Path | None = None,
    ) -> XmlResponseMetadata:
        """Download to a binary file object after a complete validated attempt."""

        temporary = self.download_to_temporary_file(
            endpoint_url, params, directory=temporary_directory
        )
        try:
            try:
                with temporary.path.open("rb") as source:
                    shutil.copyfileobj(source, destination, length=self.chunk_size)
            except OSError as error:
                _raise_local_io("downloaded response copy", error)
            return temporary.metadata
        finally:
            try:
                temporary.path.unlink(missing_ok=True)
            except OSError as error:
                _raise_local_io("temporary response file cleanup", error)


__all__ = [
    "AcademyInfoDownloadError",
    "AcademyInfoDownloader",
    "AcademyInfoLocalIOError",
    "TemporaryXmlDownload",
    "XmlResponseMetadata",
    "parse_xml_metadata",
]
