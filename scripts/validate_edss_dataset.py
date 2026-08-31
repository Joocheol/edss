#!/usr/bin/env python3
"""Validate EDSS panel outputs and school-year join coverage."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_years(value: str) -> set[str]:
    years: set[str] = set()
    for start, end in re.findall(r"((?:19|20)\d{2})(?:\s*~\s*((?:19|20)\d{2}))?", value):
        if end:
            years.update(str(year) for year in range(int(start), int(end) + 1))
        else:
            years.add(start)
    return years


def expected_years_by_logical_dataset(config: list[dict]) -> dict[tuple[str, str, str], set[str]]:
    result: dict[tuple[str, str, str], set[str]] = {}
    for entry in config:
        key = (entry["source"], entry["catalog_code"], entry["dataset"])
        result.setdefault(key, set()).update(parse_years(entry["advertised_years"]))
    return result


def profile_panel(path: Path, candidate_identifier_fields: list[str] | None = None) -> dict:
    row_count = 0
    year_counts: Counter[str] = Counter()
    missing_year = 0
    missing_open_id = 0
    candidate_identifier_fields = candidate_identifier_fields or []
    candidate_missing: Counter[str] = Counter()
    school_year_keys: set[tuple[str, str]] = set()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"_source_row_id", "_source_archive_sha256", "_panel_year", "조사년도", "개방ID"}
        missing_columns = sorted(required - set(reader.fieldnames or []))
        if missing_columns:
            raise RuntimeError(f"{path}: missing required columns: {missing_columns}")
        for row in reader:
            row_count += 1
            year = row["_panel_year"].strip()
            open_id = row["개방ID"].strip()
            if not year:
                missing_year += 1
            else:
                year_counts[year] += 1
            if not open_id:
                missing_open_id += 1
            for field in candidate_identifier_fields:
                if not row.get(field, "").strip():
                    candidate_missing[field] += 1
            if year and open_id:
                school_year_keys.add((year, open_id))
    return {
        "row_count": row_count,
        "year_counts": dict(sorted(year_counts.items())),
        "missing_panel_year": missing_year,
        "missing_open_id": missing_open_id,
        "candidate_identifier_missing_counts": dict(candidate_missing),
        "school_year_key_count": len(school_year_keys),
        "school_year_keys": school_year_keys,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/metadata/edss_panel_catalog.csv"))
    parser.add_argument("--config", type=Path, default=Path("config/edss_priority_datasets.json"))
    parser.add_argument("--output", type=Path, default=Path("data/metadata/edss_panel_validation.json"))
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    expected = expected_years_by_logical_dataset(config)
    with args.catalog.open(encoding="utf-8-sig", newline="") as handle:
        catalog = list(csv.DictReader(handle))

    inspected = []
    for item in catalog:
        path = Path(item["output_path"])
        actual_sha = sha256_file(path)
        if actual_sha != item["output_sha256"]:
            raise RuntimeError(f"checksum mismatch: {path}")
        profile_path = path.parent / "profile.json"
        build_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        result = profile_panel(path, build_profile.get("candidate_identifier_fields", []))
        if result["row_count"] != int(item["row_count"]):
            raise RuntimeError(f"row count mismatch: {path}")
        key = (item["source"], item["catalog_code"], item["dataset"])
        observed_years = set(result["year_counts"])
        result.update(
            {
                "source": item["source"],
                "catalog_code": item["catalog_code"],
                "dataset": item["dataset"],
                "access_tier": item["access_tier"],
                "output_path": item["output_path"],
                "output_sha256": actual_sha,
                "expected_years": sorted(expected[key]),
                "observed_years": sorted(observed_years),
                "missing_expected_years": sorted(expected[key] - observed_years),
                "unexpected_years": sorted(observed_years - expected[key]),
            }
        )
        inspected.append(result)

    base = next(item for item in inspected if item["catalog_code"] == "0101")
    base_keys = base["school_year_keys"]
    for item in inspected:
        keys = item.pop("school_year_keys")
        matched = len(keys & base_keys)
        orphan = len(keys - base_keys)
        item["matched_school_year_keys"] = matched
        item["orphan_school_year_keys"] = orphan
        item["school_year_join_rate"] = matched / len(keys) if keys else 0
        item["base_school_year_coverage"] = matched / len(base_keys) if base_keys else 0

    output = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass"
        if all(
            not item["missing_expected_years"]
            and not item["unexpected_years"]
            and item["missing_panel_year"] == 0
            and item["missing_open_id"] == 0
            and item["orphan_school_year_keys"] == 0
            for item in inspected
        )
        else "review_required",
        "base_school_year_dataset": "0101 고등교육학교개황",
        "datasets": inspected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".part")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(args.output)
    print(
        json.dumps(
            {
                "status": output["status"],
                "dataset_count": len(inspected),
                "total_rows": sum(item["row_count"] for item in inspected),
                "missing_years": sum(len(item["missing_expected_years"]) for item in inspected),
                "orphan_school_year_keys": sum(item["orphan_school_year_keys"] for item in inspected),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
