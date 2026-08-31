#!/usr/bin/env python3
"""Download immutable EDSS open-data files with checksums and resumable metadata."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.edmgr.kr/edss/es/opd/odd/od"
LANDING = f"{BASE}/es_opd_oddod01_001"
FILE_LIST = f"{BASE}/es_opd_oddod01_004"
DOWNLOAD = f"{BASE}/es_opd_oddod01_005"


def clean(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", value).strip(" .") or "unnamed"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_records(path: Path) -> dict[tuple[str, str], dict]:
    records: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("status") == "downloaded":
            records[(str(row.get("domn_code")), str(row.get("file_year")))] = row
    return records


def parse_filename(headers, fallback: str) -> str:
    value = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, re.I)
    if match:
        return clean(urllib.parse.unquote(match.group(1)))
    match = re.search(r'filename="?([^";]+)', value, re.I)
    if match:
        raw = match.group(1)
        try:
            raw = raw.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return clean(raw)
    return fallback


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def open_with_retry(opener, request, retries: int, delay: float):
    for attempt in range(retries + 1):
        try:
            return opener.open(request, timeout=180)
        except (urllib.error.URLError, TimeoutError):
            if attempt >= retries:
                raise
            time.sleep(delay * (2**attempt))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/edss_priority_datasets.json"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/edss"))
    parser.add_argument("--manifest", type=Path, default=Path("data/metadata/edss_file_manifest.jsonl"))
    parser.add_argument("--attempts", type=Path, default=Path("data/metadata/edss_download_attempts.jsonl"))
    parser.add_argument("--log", type=Path, default=Path("logs/edss_collection.log"))
    parser.add_argument("--priority", type=int, default=1)
    parser.add_argument("--year", default="ALL", help="ALL or one advertised year")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log, encoding="utf-8"), logging.StreamHandler()],
    )
    log = logging.getLogger("edss")

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    common_headers = {
        "User-Agent": "edss-collector/0.1 (+research; contact via repository)",
        "Referer": LANDING,
    }
    open_with_retry(opener, urllib.request.Request(LANDING, headers=common_headers), args.retries, args.delay).close()

    records = existing_records(args.manifest)
    datasets = json.loads(args.config.read_text(encoding="utf-8"))
    failures = 0
    for dataset in datasets:
        if int(dataset["priority"]) > args.priority:
            continue
        code = str(dataset["domn_code"])
        list_body = json.dumps({"domnCd": code}).encode("utf-8")
        list_request = urllib.request.Request(
            FILE_LIST,
            data=list_body,
            headers={**common_headers, "Content-Type": "application/json; charset=UTF-8"},
            method="POST",
        )
        try:
            with open_with_retry(opener, list_request, args.retries, args.delay) as response:
                available = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # recorded without secrets or response bodies
            failures += 1
            record = {
                "attempted_at": datetime.now(timezone.utc).isoformat(),
                "domn_code": code,
                "dataset": dataset["official_dataset_name"],
                "status": "file_list_failed",
                "error_type": type(exc).__name__,
                "source_url": FILE_LIST,
            }
            write_record(args.attempts, record)
            log.error("file list failed domnCd=%s dataset=%s error=%s", code, dataset["official_dataset_name"], type(exc).__name__)
            continue

        write_record(
            args.attempts,
            {
                "attempted_at": datetime.now(timezone.utc).isoformat(),
                "domn_code": code,
                "dataset": dataset["official_dataset_name"],
                "status": "file_list_verified",
                "advertised_years": [str(x.get("atflYr")) for x in available],
                "source_url": FILE_LIST,
            },
        )
        if args.list_only:
            continue
        target = next((row for row in available if str(row.get("atflYr")) == args.year), None)
        if not target:
            failures += 1
            write_record(
                args.attempts,
                {
                    "attempted_at": datetime.now(timezone.utc).isoformat(),
                    "domn_code": code,
                    "dataset": dataset["official_dataset_name"],
                    "status": "requested_year_not_advertised",
                    "requested_year": args.year,
                },
            )
            continue

        previous = records.get((code, args.year))
        if previous:
            old_path = Path(previous["local_path"])
            if old_path.exists() and sha256(old_path) == previous["sha256"]:
                log.info("skip verified domnCd=%s year=%s file=%s", code, args.year, old_path.name)
                continue

        form = urllib.parse.urlencode(
            {
                "atchFileSn": target["atchFileSn"],
                "domnCd": code,
                "atflYr": args.year,
                "ldomnNm": dataset["major_area"],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            DOWNLOAD,
            data=form,
            headers={**common_headers, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with open_with_retry(opener, request, args.retries, args.delay) as response:
                content_type = response.headers.get("Content-Type", "")
                fallback = f"{code}_{clean(dataset['official_dataset_name'])}_{args.year}.bin"
                filename = parse_filename(response.headers, fallback)
                directory = args.raw_root / clean(dataset["source"]) / f"{dataset['catalog_code']}_{clean(dataset['official_dataset_name'])}"
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / filename
                part = path.with_name(path.name + ".part")
                with part.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
            prefix = part.read_bytes()[:256].lstrip().lower()
            if "html" in content_type.lower() or prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
                part.unlink(missing_ok=True)
                raise ValueError("official endpoint returned HTML instead of a data file")
            part.replace(path)
            record = {
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
                "provider": dataset["provider"],
                "source_url": DOWNLOAD,
                "landing_url": dataset["source_url"],
                "dataset": dataset["official_dataset_name"],
                "catalog_code": dataset["catalog_code"],
                "domn_code": code,
                "file_year": args.year,
                "advertised_years": dataset["advertised_years"],
                "filename": path.name,
                "local_path": path.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "license": dataset["license"],
                "status": "downloaded",
            }
            write_record(args.manifest, record)
            log.info("downloaded domnCd=%s year=%s bytes=%d file=%s", code, args.year, path.stat().st_size, path.name)
        except Exception as exc:
            failures += 1
            status = getattr(exc, "code", None)
            write_record(
                args.attempts,
                {
                    "attempted_at": datetime.now(timezone.utc).isoformat(),
                    "domn_code": code,
                    "dataset": dataset["official_dataset_name"],
                    "requested_year": args.year,
                    "status": "download_failed",
                    "http_status": status,
                    "error_type": type(exc).__name__,
                    "source_url": DOWNLOAD,
                },
            )
            log.error("download failed domnCd=%s year=%s status=%s error=%s", code, args.year, status, type(exc).__name__)
        time.sleep(args.delay)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
