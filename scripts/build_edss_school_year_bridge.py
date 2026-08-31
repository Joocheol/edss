#!/usr/bin/env python3
"""Build a one-row-per-school-year bridge for safe EDSS panel joins.

The bridge aggregates only categorical 0101 attributes. It deliberately omits
all numeric 0101 measures so that campus-level values are never summed or
silently selected at the school-year grain.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


BASE_ATTRIBUTE_FIELDS = ("학교구분명", "시도명", "지역명", "본분교명")
BRIDGE_FIELDS = (
    "_panel_year",
    "개방ID",
    "_0101_exists",
    "_0101_match_status",
    "_review_status",
    "_0101_source_row_count",
    "_0101_branch_count",
    "_0101_branch_names",
    "_0101_province_count",
    "_0101_provinces",
    "_0101_region_count",
    "_0101_regions",
    "_0101_school_type_count",
    "_0101_school_types",
    "_0101_campus_scope",
    "_source_dataset_count",
    "_source_catalog_codes",
    "_source_row_count",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_catalog(catalog_path: Path) -> list[dict[str, str]]:
    with catalog_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"empty catalog: {catalog_path}")
    return rows


def resolve_panel_path(catalog_path: Path, output_path: str) -> Path:
    path = Path(output_path)
    if path.is_absolute():
        return path
    # The catalog is stored at data/metadata/<name>.csv.
    return catalog_path.resolve().parents[2] / path


def panel_rows(path: Path, optional_fields: tuple[str, ...] = ()):
    """Yield year, open ID, and selected fields while streaming a gzip panel."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        positions = {name: index for index, name in enumerate(header)}
        required = {"_panel_year", "개방ID"}
        missing = sorted(required - positions.keys())
        if missing:
            raise RuntimeError(f"{path}: missing columns: {missing}")
        missing_optional = sorted(set(optional_fields) - positions.keys())
        if missing_optional:
            raise RuntimeError(f"{path}: missing 0101 attribute columns: {missing_optional}")
        selected = {name: positions[name] for name in optional_fields}
        year_index = positions["_panel_year"]
        open_id_index = positions["개방ID"]
        for row in reader:
            values = {name: row[index].strip() for name, index in selected.items()}
            yield row[year_index].strip(), row[open_id_index].strip(), values


def classify_orphan(year: int, base_years: set[int]) -> str:
    if not base_years:
        return "open_id_absent_all_years"
    if year < min(base_years):
        return "before_base_first_seen"
    if year > max(base_years):
        return "after_base_last_seen"
    return "internal_base_gap"


def review_status(match_status: str) -> str:
    return {
        "matched": "not_required",
        "before_base_first_seen": "unresolved_temporal_boundary",
        "after_base_last_seen": "unresolved_temporal_boundary",
        "internal_base_gap": "external_crosscheck_required_internal_gap",
        "open_id_absent_all_years": "external_crosscheck_required_absent_all_years",
    }[match_status]


def campus_scope(base_row_count: int, branch_count: int) -> str:
    if base_row_count == 0:
        return "not_observed"
    if branch_count == 0:
        return "unknown"
    if branch_count == 1:
        return "single_campus"
    return "multiple_campuses"


def validate_catalog_checksums(catalog_path: Path, catalog: list[dict[str, str]]) -> list[dict[str, str]]:
    results = []
    for item in catalog:
        expected = item.get("output_sha256", "").strip()
        path = resolve_panel_path(catalog_path, item["output_path"])
        actual = sha256_file(path)
        results.append(
            {
                "catalog_code": item["catalog_code"],
                "dataset": item["dataset"],
                "expected_sha256": expected,
                "actual_sha256": actual,
                "matches": bool(expected) and expected == actual,
            }
        )
    mismatches = [row for row in results if not row["matches"]]
    if mismatches:
        labels = ", ".join(f"{row['catalog_code']} {row['dataset']}" for row in mismatches)
        raise RuntimeError(f"panel checksum validation failed: {labels}")
    return results


def validate_bridge_records(records: list[dict]) -> dict:
    key_counts = Counter((row["_panel_year"], row["개방ID"]) for row in records)
    duplicate_keys = [key for key, count in key_counts.items() if count > 1]
    blank_keys = [key for key in key_counts if not key[0] or not key[1]]
    consistency_errors = []
    for row in records:
        exists = row["_0101_exists"] == "true"
        status = row["_0101_match_status"]
        base_rows = int(row["_0101_source_row_count"])
        branches = int(row["_0101_branch_count"])
        expected_scope = campus_scope(base_rows, branches)
        if exists != (status == "matched"):
            consistency_errors.append((row["_panel_year"], row["개방ID"], "existence/status"))
        if exists != (base_rows > 0):
            consistency_errors.append((row["_panel_year"], row["개방ID"], "existence/base rows"))
        if row["_0101_campus_scope"] != expected_scope:
            consistency_errors.append((row["_panel_year"], row["개방ID"], "campus scope"))
        if row["_review_status"] != review_status(status):
            consistency_errors.append((row["_panel_year"], row["개방ID"], "review status"))
    if duplicate_keys or blank_keys or consistency_errors:
        raise ValueError(
            "invalid bridge: "
            f"duplicate_keys={len(duplicate_keys)}, blank_keys={len(blank_keys)}, "
            f"consistency_errors={len(consistency_errors)}"
        )
    return {
        "key_columns": ["_panel_year", "개방ID"],
        "row_count": len(records),
        "distinct_key_count": len(key_counts),
        "duplicate_key_count": 0,
        "blank_key_count": 0,
        "max_key_multiplicity": max(key_counts.values(), default=0),
        "unique_key": True,
    }


def build_bridge(catalog_path: Path, base_code: str = "0101") -> tuple[dict, list[dict]]:
    catalog = read_catalog(catalog_path)
    base_item = next((item for item in catalog if item["catalog_code"] == base_code), None)
    if base_item is None:
        raise RuntimeError(f"base catalog code not found: {base_code}")

    base_path = resolve_panel_path(catalog_path, base_item["output_path"])
    base_by_key: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "row_count": 0,
            "학교구분명": set(),
            "시도명": set(),
            "지역명": set(),
            "본분교명": set(),
        }
    )
    base_id_years: dict[str, set[int]] = defaultdict(set)
    base_natural_key_rows: Counter[tuple[str, str, str]] = Counter()
    for year, open_id, values in panel_rows(base_path, BASE_ATTRIBUTE_FIELDS):
        if not year or not open_id:
            continue
        key = (year, open_id)
        aggregate = base_by_key[key]
        aggregate["row_count"] += 1
        base_natural_key_rows[(year, open_id, values["본분교명"])] += 1
        base_id_years[open_id].add(int(year))
        for field in BASE_ATTRIBUTE_FIELDS:
            value = values[field]
            if value:
                aggregate[field].add(value)

    repeated_natural_keys = [key for key, count in base_natural_key_rows.items() if count > 1]
    if repeated_natural_keys:
        raise RuntimeError(
            "0101 natural key is not unique: "
            f"repeated (_panel_year, 개방ID, 본분교명) keys={len(repeated_natural_keys)}"
        )

    key_sources: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"datasets": set(), "row_count": 0}
    )
    dataset_summaries = []
    for item in catalog:
        path = resolve_panel_path(catalog_path, item["output_path"])
        catalog_code = item["catalog_code"]
        input_rows = 0
        missing_key_rows = 0
        matched_0101_rows = 0
        unmatched_0101_rows = 0
        for year, open_id, _ in panel_rows(path):
            input_rows += 1
            if not year or not open_id:
                missing_key_rows += 1
                continue
            key = (year, open_id)
            key_sources[key]["datasets"].add(catalog_code)
            key_sources[key]["row_count"] += 1
            if key in base_by_key:
                matched_0101_rows += 1
            else:
                unmatched_0101_rows += 1
        nonempty_key_rows = input_rows - missing_key_rows
        dataset_summaries.append(
            {
                "catalog_code": item["catalog_code"],
                "dataset": item["dataset"],
                "input_row_count": input_rows,
                "nonempty_join_key_row_count": nonempty_key_rows,
                "missing_join_key_row_count": missing_key_rows,
                "matched_0101_row_count": matched_0101_rows,
                "unmatched_0101_row_count": unmatched_0101_rows,
                "left_join_output_row_count": input_rows,
                "row_expansion_count": 0,
            }
        )

    records = []
    for (year, open_id), source in sorted(key_sources.items()):
        base = base_by_key.get((year, open_id))
        base_row_count = int(base["row_count"]) if base else 0
        branches = sorted(base["본분교명"]) if base else []
        provinces = sorted(base["시도명"]) if base else []
        regions = sorted(base["지역명"]) if base else []
        school_types = sorted(base["학교구분명"]) if base else []
        match_status = "matched" if base else classify_orphan(int(year), base_id_years.get(open_id, set()))
        records.append(
            {
                "_panel_year": year,
                "개방ID": open_id,
                "_0101_exists": "true" if base else "false",
                "_0101_match_status": match_status,
                "_review_status": review_status(match_status),
                "_0101_source_row_count": base_row_count,
                "_0101_branch_count": len(branches),
                "_0101_branch_names": "|".join(branches),
                "_0101_province_count": len(provinces),
                "_0101_provinces": "|".join(provinces),
                "_0101_region_count": len(regions),
                "_0101_regions": "|".join(regions),
                "_0101_school_type_count": len(school_types),
                "_0101_school_types": "|".join(school_types),
                "_0101_campus_scope": campus_scope(base_row_count, len(branches)),
                "_source_dataset_count": len(source["datasets"]),
                "_source_catalog_codes": "|".join(sorted(source["datasets"])),
                "_source_row_count": source["row_count"],
            }
        )

    validation = validate_bridge_records(records)
    match_counts = Counter(row["_0101_match_status"] for row in records)
    review_counts = Counter(row["_review_status"] for row in records)
    campus_counts = Counter(row["_0101_campus_scope"] for row in records)
    base_key_multiplicities = [int(row["row_count"]) for row in base_by_key.values()]
    input_row_count = sum(row["input_row_count"] for row in dataset_summaries)
    missing_key_row_count = sum(row["missing_join_key_row_count"] for row in dataset_summaries)
    unmatched_row_count = sum(row["unmatched_0101_row_count"] for row in dataset_summaries)
    summary = {
        "generated_at": utc_now(),
        "status": "review_required" if match_counts.get("matched", 0) != len(records) else "pass",
        "grain": "one row per distinct nonempty (_panel_year, 개방ID) observed in any cataloged panel",
        "base_dataset": f"{base_code} {base_item['dataset']}",
        "bridge_validation": validation,
        "base_natural_key": ["_panel_year", "개방ID", "본분교명"],
        "base_source_row_count": len(base_natural_key_rows),
        "base_natural_key_count": len(base_natural_key_rows),
        "base_repeated_natural_key_count": 0,
        "base_school_year_key_count": len(base_by_key),
        "base_repeated_school_year_key_count": sum(count > 1 for count in base_key_multiplicities),
        "base_max_school_year_key_multiplicity": max(base_key_multiplicities, default=0),
        "match_status_counts": dict(sorted(match_counts.items())),
        "review_status_counts": dict(sorted(review_counts.items())),
        "campus_scope_counts": dict(sorted(campus_counts.items())),
        "source_dataset_count": len(catalog),
        "source_input_row_count": input_row_count,
        "source_nonempty_join_key_row_count": input_row_count - missing_key_row_count,
        "source_missing_join_key_row_count": missing_key_row_count,
        "source_unmatched_0101_row_count": unmatched_row_count,
        "left_join_validation": {
            "status": "pass",
            "rule": "A left join from any panel to this unique bridge on (_panel_year, 개방ID) preserves every source row and cannot expand rows.",
            "source_row_count": input_row_count,
            "simulated_left_join_row_count": input_row_count,
            "row_expansion_count": 0,
        },
        "datasets": dataset_summaries,
        "aggregation_rule": "Only distinct nonempty categorical 0101 values are listed. Numeric 0101 measures are excluded and never summed or selected.",
        "orphan_rule": "Unmatched keys remain in the bridge with _0101_exists=false and their temporal review status. They are never deleted or mapped to an adjacent year.",
        "missing_key_rule": "Source rows with a blank year or open ID remain on the left side of downstream joins; no synthetic blank-key bridge row is created.",
    }
    return summary, records


def atomic_write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BRIDGE_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    temp.replace(path)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/metadata/edss_panel_catalog.csv"))
    parser.add_argument("--base-code", default="0101")
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/metadata/edss_school_year_bridge.csv"),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("data/metadata/edss_school_year_bridge_summary.json"),
    )
    parser.add_argument(
        "--skip-input-checksums",
        action="store_true",
        help="Skip catalog SHA-256 verification before scanning panels.",
    )
    args = parser.parse_args()

    catalog = read_catalog(args.catalog)
    checksum_results = []
    if not args.skip_input_checksums:
        checksum_results = validate_catalog_checksums(args.catalog, catalog)
    summary, records = build_bridge(args.catalog, args.base_code)
    summary["input_checksum_validation"] = {
        "status": "skipped" if args.skip_input_checksums else "pass",
        "checked_file_count": len(checksum_results),
        "mismatch_count": 0,
    }
    atomic_write_csv(args.output_csv, records)
    summary["output_csv"] = str(args.output_csv)
    summary["output_csv_bytes"] = args.output_csv.stat().st_size
    summary["output_csv_sha256"] = sha256_file(args.output_csv)
    atomic_write_json(args.output_json, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
