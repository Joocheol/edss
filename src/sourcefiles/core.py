"""Small, dependency-free primitives for resumable immutable downloads."""

from __future__ import annotations

import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Callable, Mapping


class CollectionError(RuntimeError):
    """A source request or collection contract failed."""


class IntegrityError(CollectionError):
    """A local or downloaded file did not satisfy integrity checks."""


def _truncate_utf8(value: str, max_bytes: int) -> str:
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    suffix = Path(value).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
        suffix = ""
    budget = max_bytes - len(suffix.encode("utf-8"))
    stem = value[: -len(suffix)] if suffix else value
    shortened = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .")
    return shortened + suffix


def clean_component(value: str, *, fallback: str = "unnamed", max_bytes: int = 240) -> str:
    if max_bytes < 16:
        raise ValueError("max_bytes must be at least 16")
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value).strip(" .")
    return _truncate_utf8(cleaned or fallback, max_bytes)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_content_disposition(headers: Message, fallback: str) -> str:
    value = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, re.IGNORECASE)
    if match:
        return clean_component(urllib.parse.unquote(match.group(1)))
    match = re.search(r'filename="?([^";]+)', value, re.IGNORECASE)
    if not match:
        return clean_component(fallback)
    raw = match.group(1)
    if re.search(r"%[0-9A-Fa-f]{2}", raw):
        raw = urllib.parse.unquote(raw)
    else:
        try:
            raw = raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return clean_component(raw)


def append_jsonl(path: Path, record: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode()
    with path.open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


class Manifest:
    """Index successful JSONL records and verify files before resuming."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict] = {}
        if not path.exists():
            return
        lines = path.read_bytes().splitlines()
        for index, raw in enumerate(lines):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if index == len(lines) - 1:
                    break
                raise IntegrityError(f"invalid manifest line {index + 1}: {path}") from exc
            task_id = row.get("task_id")
            if row.get("status") == "downloaded" and task_id:
                self.records[str(task_id)] = row

    def verified(self, task_id: str) -> bool:
        row = self.records.get(task_id)
        if not row:
            return False
        path = Path(str(row.get("local_path", "")))
        if not path.is_file():
            raise IntegrityError(f"manifest file is missing: {path}")
        expected_size = int(row.get("size_bytes", -1))
        if path.stat().st_size != expected_size:
            raise IntegrityError(f"manifest size mismatch: {path}")
        if sha256_file(path) != row.get("sha256"):
            raise IntegrityError(f"manifest checksum mismatch: {path}")
        return True

    def add(self, record: dict) -> None:
        append_jsonl(self.path, record)
        self.records[str(record["task_id"])] = record


class HttpSession:
    """Cookie-preserving urllib session with bounded exponential retries."""

    def __init__(
        self,
        *,
        user_agent: str,
        retries: int = 2,
        retry_delay: float = 1.0,
        timeout: float = 180.0,
        sleeper: Callable[[float], None] = time.sleep,
        opener=None,
    ) -> None:
        if retries < 0 or retry_delay < 0 or timeout <= 0:
            raise ValueError("invalid HTTP retry settings")
        self.retries = retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.sleeper = sleeper
        self.headers = {"User-Agent": user_agent}
        if opener is None:
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self.opener = opener

    def open(
        self,
        url: str,
        *,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        method: str | None = None,
    ):
        merged = {**self.headers, **(dict(headers or {}))}
        request = urllib.request.Request(url, data=data, headers=merged, method=method)
        for attempt in range(self.retries + 1):
            try:
                return self.opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if attempt >= self.retries or not retryable:
                    raise
                retry_after = exc.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else self.retry_delay * (2**attempt)
                self.sleeper(min(wait, 300.0))
            except (urllib.error.URLError, TimeoutError):
                if attempt >= self.retries:
                    raise
                self.sleeper(self.retry_delay * (2**attempt))
        raise AssertionError("unreachable")

    def get_text(self, url: str, *, headers: Mapping[str, str] | None = None) -> str:
        with self.open(url, headers=headers) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="strict")

    def post_json(self, url: str, payload: Mapping[str, object], *, referer: str) -> object:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        }
        with self.open(url, data=data, headers=headers, method="POST") as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))


@dataclass(frozen=True)
class StoredFile:
    path: Path
    size_bytes: int
    sha256: str
    filename: str


def _looks_like_html(prefix: bytes, content_type: str) -> bool:
    lowered = prefix.lstrip().lower()
    return "html" in content_type.lower() or lowered.startswith((b"<!doctype html", b"<html"))


def _validate_archive(path: Path, kind: str) -> None:
    if kind not in {"zip", "xlsx", "any"}:
        raise ValueError(f"unknown archive kind: {kind}")
    if kind == "any":
        return
    if not zipfile.is_zipfile(path):
        raise IntegrityError(f"download is not a valid {kind} archive")
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            raise IntegrityError(f"corrupt archive member: {bad}")
        if kind == "xlsx":
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required.issubset(names):
                raise IntegrityError("download is a ZIP but not an XLSX workbook")


def store_response(
    response,
    destination: Path,
    *,
    archive_kind: str,
    fallback_filename: str,
    chunk_size: int = 1024 * 1024,
) -> StoredFile:
    """Stream a response to a temporary file, validate, then publish atomically."""
    destination.mkdir(parents=True, exist_ok=True)
    filename = parse_content_disposition(response.headers, fallback_filename)
    final = destination / filename
    part = destination / f".{filename}.part"
    if part.exists():
        part.unlink()
    try:
        with part.open("wb") as stream:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if not part.stat().st_size:
            raise IntegrityError("downloaded file is empty")
        with part.open("rb") as stream:
            prefix = stream.read(512)
        if _looks_like_html(prefix, response.headers.get("Content-Type", "")):
            raise IntegrityError("source returned HTML instead of a data file")
        _validate_archive(part, archive_kind)
        digest = sha256_file(part)
        size = part.stat().st_size
        if final.exists():
            if final.stat().st_size == size and sha256_file(final) == digest:
                part.unlink()
            else:
                raise IntegrityError(f"refusing to overwrite a different file: {final}")
        else:
            part.replace(final)
        return StoredFile(final, size, digest, filename)
    except Exception:
        part.unlink(missing_ok=True)
        raise


def store_local_file(
    source: Path,
    destination: Path,
    *,
    archive_kind: str,
    filename: str,
) -> StoredFile:
    """Validate and atomically publish a file produced by a browser download."""
    if not source.is_file():
        raise IntegrityError(f"browser download is missing: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    clean_name = clean_component(filename)
    final = destination / clean_name
    part = destination / f".{clean_name}.part"
    if part.exists():
        part.unlink()
    try:
        shutil.copyfile(source, part)
        if not part.stat().st_size:
            raise IntegrityError("downloaded file is empty")
        _validate_archive(part, archive_kind)
        digest = sha256_file(part)
        size = part.stat().st_size
        if final.exists():
            if final.stat().st_size == size and sha256_file(final) == digest:
                part.unlink()
            else:
                raise IntegrityError(f"refusing to overwrite a different file: {final}")
        else:
            part.replace(final)
        return StoredFile(final, size, digest, clean_name)
    except Exception:
        part.unlink(missing_ok=True)
        raise
