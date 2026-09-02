#!/usr/bin/env python3
"""Collect 2023-2024 AcademyInfo school codes and enrollment counts safely.

The AcademyInfo student comparison endpoint requires one school ID per request.
This collector first obtains the year-specific university-code list, then stores
one raw XML response per school and year. Raw responses are resumable, the API
key is never logged, and every cached or newly collected file is checksummed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import collect_api


CODE_OPERATION = "/getUniversityCode"
ENROLLMENT_OPERATION = "/getComparisonEnrolledStudentCrntSt"
OUTPUT_FIELDS = (
    "svyYr",
    "schlId",
    "schlKrnNm",
    "schlFullNm",
    "clgcpDivCd",
    "clgcpDivNm",
    "schlDivCd",
    "schlDivNm",
    "schlKndCd",
    "schlKndNm",
    "znCd",
    "znNm",
    "estbDivCd",
    "estbDivNm",
    "indctId",
    "indctVal1",
    "enrollment_response_count",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    records: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        records[record["relative_path"]] = record
    return records


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def write_manifest(path: Path, records: list[dict]) -> None:
    payload = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    atomic_write_text(path, payload)


def record_file(
    path: Path,
    parsed: dict,
    request_type: str,
    year: int,
    school_id: str,
    key_interpretation: str,
    previous: dict[str, dict],
) -> dict:
    relative_path = path.as_posix()
    prior = previous.get(relative_path, {})
    return {
        "request_type": request_type,
        "year": year,
        "school_id": school_id,
        "result_code": parsed["result_code"],
        "result_message": parsed["result_message"],
        "total_count": parsed["total_count"],
        "item_count": len(parsed["items"]),
        "relative_path": relative_path,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "collected_at": prior.get("collected_at", utc_now()),
        "key_interpretation": prior.get("key_interpretation", key_interpretation),
    }


def fetch(
    service: dict,
    operation: dict,
    params: dict[str, str],
    path: Path,
    raw_key: str,
    args: argparse.Namespace,
    budget: list[int],
) -> tuple[dict, str]:
    cached = collect_api.valid_cached_page(path)
    if cached:
        _data, parsed = cached
        return parsed, "cached"
    if budget[0] >= args.max_requests:
        raise RuntimeError("request budget exhausted before collection completed")
    budget[0] += 1
    data, parsed, key_interpretation = collect_api.request_page(
        service,
        operation,
        params,
        raw_key,
        args.key_mode,
        args.retries,
        args.timeout,
        args.delay,
    )
    collect_api.atomic_write(path, data)
    if args.progress_every and budget[0] % args.progress_every == 0:
        print(f"progress requests={budget[0]} last={params.get('svyYr', '')}/{params.get('schlId', 'codes')}", flush=True)
    time.sleep(args.delay)
    return parsed, key_interpretation


def collect_code_list(
    year: int,
    service: dict,
    operation: dict,
    root: Path,
    raw_key: str,
    args: argparse.Namespace,
    budget: list[int],
    previous: dict[str, dict],
) -> tuple[list[dict[str, str]], list[dict]]:
    page = 1
    items: list[dict[str, str]] = []
    records: list[dict] = []
    while True:
        path = root / str(year) / "codes" / f"page_{page:04d}.xml"
        params = {"pageNo": str(page), "numOfRows": str(args.num_rows), "svyYr": str(year)}
        parsed, key_interpretation = fetch(service, operation, params, path, raw_key, args, budget)
        records.append(record_file(path, parsed, "university_codes", year, "", key_interpretation, previous))
        items.extend(parsed["items"])
        total = parsed["total_count"]
        if not parsed["items"] or len(items) >= total or page * args.num_rows >= total:
            break
        page += 1
    ids = [item.get("schlId", "") for item in items]
    if not items or any(not value for value in ids):
        raise RuntimeError(f"{year}: university-code response is empty or has blank school IDs")
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"{year}: university-code response has duplicate school IDs")
    return items, records


def collect_enrollment(
    code: dict[str, str],
    year: int,
    service: dict,
    operation: dict,
    root: Path,
    raw_key: str,
    args: argparse.Namespace,
    budget: list[int],
    previous: dict[str, dict],
) -> tuple[dict[str, str], dict]:
    school_id = code["schlId"]
    path = root / str(year) / "enrollment" / f"{school_id}.xml"
    params = {
        "pageNo": "1",
        "numOfRows": str(args.num_rows),
        "schlId": school_id,
        "svyYr": str(year),
    }
    parsed, key_interpretation = fetch(service, operation, params, path, raw_key, args, budget)
    items = parsed["items"]
    if len(items) > 1:
        raise RuntimeError(f"{year}/{school_id}: expected at most one enrollment item, got {len(items)}")
    metric = items[0] if items else {}
    if metric and (metric.get("schlId") != school_id or metric.get("svyYr") != str(year)):
        raise RuntimeError(f"{year}/{school_id}: response key does not match request")
    row = {field: code.get(field, "") for field in OUTPUT_FIELDS}
    for field in ("indctId", "indctVal1"):
        row[field] = metric.get(field, "")
    row["enrollment_response_count"] = str(len(items))
    record = record_file(path, parsed, "enrollment", year, school_id, key_interpretation, previous)
    return row, record


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    with partial.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(partial, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/api_services.json"))
    parser.add_argument("--years", nargs="+", type=int, default=[2023, 2024])
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/open_api/academyinfo_enrollment"))
    parser.add_argument("--manifest", type=Path, default=Path("data/metadata/academyinfo_enrollment_manifest.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("data/metadata/academyinfo_enrollment_collection.json"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/open_api/academyinfo_enrollment/academyinfo_enrollment_2023_2024.csv"))
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--num-rows", type=int, default=9999)
    parser.add_argument("--key-mode", choices=("auto", "decoded", "encoded"), default="auto")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--max-requests", type=int, default=1000)
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect_api.load_dotenv(args.dotenv)
    raw_key, key_source = collect_api.service_key()
    if not raw_key:
        raise SystemExit("no API key found; set DATA_GO_KR_SERVICE_KEY in local .env")
    logging.info("using API key from %s (value redacted)", key_source)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    basic_service = collect_api.choose_service(config, "basic_information")
    basic_operation = collect_api.resolve_operation(basic_service, CODE_OPERATION)
    student_service = collect_api.choose_service(config, "student")
    student_operation = collect_api.resolve_operation(student_service, ENROLLMENT_OPERATION)
    previous = existing_manifest(args.manifest)
    budget = [0]
    all_rows: list[dict[str, str]] = []
    records: list[dict] = []
    year_summaries: list[dict] = []

    for year in args.years:
        codes, code_records = collect_code_list(
            year,
            basic_service,
            basic_operation,
            args.raw_root,
            raw_key,
            args,
            budget,
            previous,
        )
        records.extend(code_records)
        year_rows: list[dict[str, str]] = []
        for code in sorted(codes, key=lambda row: row["schlId"]):
            row, record = collect_enrollment(
                code,
                year,
                student_service,
                student_operation,
                args.raw_root,
                raw_key,
                args,
                budget,
                previous,
            )
            year_rows.append(row)
            records.append(record)
        all_rows.extend(year_rows)
        nonempty = [row for row in year_rows if row["enrollment_response_count"] == "1"]
        numeric = [row for row in nonempty if row["indctVal1"].isdigit()]
        year_summaries.append(
            {
                "year": year,
                "school_code_count": len(codes),
                "enrollment_response_count": len(nonempty),
                "numeric_enrollment_count": len(numeric),
                "blank_or_nonnumeric_count": len(codes) - len(numeric),
            }
        )
        print(
            f"year={year} codes={len(codes)} enrollment={len(nonempty)} numeric={len(numeric)} requests={budget[0]}",
            flush=True,
        )

    all_rows.sort(key=lambda row: (row["svyYr"], row["schlId"]))
    write_csv(args.output, all_rows)
    records.sort(key=lambda record: record["relative_path"])
    write_manifest(args.manifest, records)
    duplicate_keys = len(all_rows) - len({(row["svyYr"], row["schlId"]) for row in all_rows})
    report = {
        "status": "complete" if duplicate_keys == 0 else "review_required",
        "source": "한국대학교육협의회 대학알리미 Open API",
        "code_service": basic_service["base_url"] + CODE_OPERATION,
        "enrollment_service": student_service["base_url"] + ENROLLMENT_OPERATION,
        "years": args.years,
        "generated_at": utc_now(),
        "request_count_this_run": budget[0],
        "raw_file_count": len(records),
        "aggregate_row_count": len(all_rows),
        "duplicate_school_year_key_count": duplicate_keys,
        "aggregate_relative_path": args.output.as_posix(),
        "aggregate_bytes": args.output.stat().st_size,
        "aggregate_sha256": sha256_file(args.output),
        "year_summaries": year_summaries,
        "metric_definition": {
            "api_operation": ENROLLMENT_OPERATION,
            "indicator_id": sorted({row["indctId"] for row in all_rows if row["indctId"]}),
            "value_field": "indctVal1",
            "label": "재적학생 현황",
        },
    }
    atomic_write_text(args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
