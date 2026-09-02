#!/usr/bin/env python3
"""Collect the AcademyInfo school-major feed with graduate-school QA.

The source returns one current survey year at a time and does not expose a
graduate-school identifier.  Raw XML and aggregate CSV files stay under
``data/raw``; the small manifest and quality report are safe to version.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import collect_api


SERVICE_KEY = "department_by_university"
OPERATION = "/getSchoolMajorInfo"
GRADUATE_KINDS = {"일반대학원", "전문대학원", "특수대학원"}
PREFERRED_FIELDS = (
    "svyYr",
    "schlNm",
    "schlKndNm",
    "clgNm",
    "korMjrNm",
    "pbnfDgriCrseDivNm",
    "lsnTrmNm",
    "dghtDivNm",
    "schlMjrCharNm",
    "schlMjrStatNm",
    "mjrAreaNm",
    "mjrAreaSignguNm",
    "eschlPscpNum",
    "grdtNum",
    "kediMjrId",
    "stdClftMjrId",
    "mjrAreaCd",
    "mjrAreaSignguCd",
    "onsfSrsClftNm",
    "edcCrseLtrCtnt",
    "pwayEmplLtrCtnt",
    "mjrUpdtDtm",
    "lstUpdtDtm",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    with partial.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    os.replace(partial, path)


def existing_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["relative_path"]] = record
    return records


def collect_year(
    year: int,
    service: dict,
    operation: dict,
    raw_root: Path,
    raw_key: str,
    args: argparse.Namespace,
    budget: list[int],
    previous: dict[str, dict],
) -> tuple[list[dict[str, str]], list[dict]]:
    items: list[dict[str, str]] = []
    records: list[dict] = []
    page = 1
    expected_total: int | None = None
    while True:
        path = raw_root / str(year) / f"page_{page:04d}.xml"
        cached = collect_api.valid_cached_page(path)
        key_interpretation = "cached"
        if cached:
            data, parsed = cached
        else:
            if budget[0] >= args.max_requests:
                raise RuntimeError("request budget exhausted before collection completed")
            params = {
                "pageNo": str(page),
                "numOfRows": str(args.num_rows),
                "svyYr": str(year),
            }
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
            time.sleep(args.delay)
        if expected_total is None:
            expected_total = parsed["total_count"]
        elif parsed["total_count"] != expected_total:
            raise RuntimeError(
                f"{year}: totalCount changed across pages: {expected_total} -> {parsed['total_count']}"
            )
        prior = previous.get(path.as_posix(), {})
        records.append(
            {
                "request_type": "school_major",
                "year": year,
                "page": page,
                "result_code": parsed["result_code"],
                "result_message": parsed["result_message"],
                "total_count": parsed["total_count"],
                "item_count": len(parsed["items"]),
                "relative_path": path.as_posix(),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "collected_at": prior.get("collected_at", utc_now()),
                "key_interpretation": prior.get("key_interpretation", key_interpretation),
            }
        )
        items.extend(parsed["items"])
        if args.progress_every and page % args.progress_every == 0:
            print(f"progress year={year} pages={page} rows={len(items)}/{expected_total}", flush=True)
        if not parsed["items"] or len(items) >= expected_total:
            break
        page += 1

    if expected_total != len(items):
        raise RuntimeError(f"{year}: expected {expected_total} rows but collected {len(items)}")
    return items, records


def ordered_fields(rows: list[dict[str, str]]) -> list[str]:
    observed = {field for row in rows for field in row}
    return [field for field in PREFERRED_FIELDS if field in observed] + sorted(observed - set(PREFERRED_FIELDS))


def quality_summary(rows: list[dict[str, str]], year: int) -> dict:
    graduates = [row for row in rows if row.get("schlKndNm") in GRADUATE_KINDS]
    exact_fingerprints = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    graduate_key = [
        (
            row.get("svyYr", ""),
            row.get("schlNm", ""),
            row.get("schlKndNm", ""),
            row.get("korMjrNm", ""),
            row.get("pbnfDgriCrseDivNm", ""),
            row.get("dghtDivNm", ""),
            row.get("schlMjrStatNm", ""),
        )
        for row in graduates
    ]
    fields = ordered_fields(rows)
    identifier_fields = sorted(field for field in fields if field.lower().endswith("id"))
    return {
        "year": year,
        "row_count": len(rows),
        "column_count": len(fields),
        "graduate_row_count": len(graduates),
        "graduate_row_rate": round(len(graduates) / len(rows), 6) if rows else 0,
        "graduate_school_count": len({row.get("schlNm", "") for row in graduates if row.get("schlNm")}),
        "graduate_kind_counts": dict(sorted(Counter(row.get("schlKndNm", "") for row in graduates).items())),
        "graduate_degree_counts": dict(
            sorted(Counter(row.get("pbnfDgriCrseDivNm", "") for row in graduates).items())
        ),
        "blank_school_name_count": sum(not row.get("schlNm") for row in rows),
        "blank_major_name_count": sum(not row.get("korMjrNm") for row in rows),
        "exact_duplicate_row_count": len(exact_fingerprints) - len(set(exact_fingerprints)),
        "graduate_candidate_key_duplicate_row_count": len(graduate_key) - len(set(graduate_key)),
        "observed_fields": fields,
        "identifier_like_fields": identifier_fields,
        "graduate_school_identifier_present": any(
            field in fields for field in ("schlId", "graduateSchlId", "grdtSchlId")
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/api_services.json"))
    parser.add_argument("--years", nargs="+", type=int, default=[2025])
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/open_api/academyinfo_school_major"))
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/metadata/academyinfo_school_major_manifest.jsonl")
    )
    parser.add_argument(
        "--report", type=Path, default=Path("data/metadata/academyinfo_school_major_collection.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/raw/open_api/academyinfo_school_major/school_major.csv")
    )
    parser.add_argument(
        "--graduate-output",
        type=Path,
        default=Path("data/raw/open_api/academyinfo_school_major/graduate_school_major.csv"),
    )
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--num-rows", type=int, default=9999)
    parser.add_argument("--key-mode", choices=("auto", "decoded", "encoded"), default="auto")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--max-requests", type=int, default=100)
    parser.add_argument("--progress-every", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collect_api.load_dotenv(args.dotenv)
    raw_key, key_source = collect_api.service_key()
    if not raw_key:
        raise SystemExit("no API key found; set DATA_GO_KR_SERVICE_KEY in local .env")
    logging.info("using API key from %s (value redacted)", key_source)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    service = collect_api.choose_service(config, SERVICE_KEY)
    operation = collect_api.resolve_operation(service, OPERATION)
    previous = existing_manifest(args.manifest)
    budget = [0]
    rows: list[dict[str, str]] = []
    records: list[dict] = []
    summaries: list[dict] = []
    for year in args.years:
        year_rows, year_records = collect_year(
            year, service, operation, args.raw_root, raw_key, args, budget, previous
        )
        rows.extend(year_rows)
        records.extend(year_records)
        summaries.append(quality_summary(year_rows, year))

    fields = ordered_fields(rows)
    rows.sort(
        key=lambda row: (
            row.get("svyYr", ""),
            row.get("schlNm", ""),
            row.get("korMjrNm", ""),
            row.get("pbnfDgriCrseDivNm", ""),
        )
    )
    graduate_rows = [row for row in rows if row.get("schlKndNm") in GRADUATE_KINDS]
    write_csv(args.output, rows, fields)
    write_csv(args.graduate_output, graduate_rows, fields)
    records.sort(key=lambda record: record["relative_path"])
    atomic_write_text(
        args.manifest,
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
    )
    report = {
        "status": "complete"
        if all(summary["exact_duplicate_row_count"] == 0 for summary in summaries)
        else "review_required",
        "source": "한국대학교육협의회_대학별 학과정보_GW",
        "dataset_id": service["id"],
        "service": service["base_url"] + OPERATION,
        "years": args.years,
        "generated_at": utc_now(),
        "request_count_this_run": budget[0],
        "raw_file_count": len(records),
        "aggregate": {
            "relative_path": args.output.as_posix(),
            "row_count": len(rows),
            "bytes": args.output.stat().st_size,
            "sha256": sha256_file(args.output),
        },
        "graduate_subset": {
            "relative_path": args.graduate_output.as_posix(),
            "row_count": len(graduate_rows),
            "bytes": args.graduate_output.stat().st_size,
            "sha256": sha256_file(args.graduate_output),
            "filter": "schlKndNm in 일반대학원, 전문대학원, 특수대학원",
        },
        "year_summaries": summaries,
        "limitations": [
            "The live endpoint currently returns records only for survey year 2025; 2009-2024 returned normal empty responses during validation.",
            "The response names graduate schools but does not expose a graduate-school or parent-university identifier.",
            "eschlPscpNum is admission quota and grdtNum is graduates; neither is current enrollment.",
            "Any EDSS OpenID linkage produced from names, departments, or counts is a review candidate rather than an official crosswalk.",
            "Exact duplicate source rows are preserved in the raw aggregate and reported rather than silently removed.",
        ],
    }
    atomic_write_text(args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
