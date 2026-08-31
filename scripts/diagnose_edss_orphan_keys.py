#!/usr/bin/env python3
"""Diagnose EDSS school-year keys that do not join to the 0101 base panel.

The output is deliberately key-level. It does not copy measures or person-level
fields from the restricted employment panel.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


BASE_ATTRIBUTE_FIELDS = ("학교구분명", "시도명", "지역명", "본분교명")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def panel_rows(path: Path, optional_fields: tuple[str, ...] = ()):
    """Yield the join key and selected fields without materializing full rows."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        positions = {name: index for index, name in enumerate(header)}
        required = {"_panel_year", "개방ID"}
        missing = sorted(required - positions.keys())
        if missing:
            raise RuntimeError(f"{path}: missing columns: {missing}")
        selected = {name: positions[name] for name in optional_fields if name in positions}
        year_index = positions["_panel_year"]
        open_id_index = positions["개방ID"]
        for row in reader:
            year = row[year_index].strip()
            open_id = row[open_id_index].strip()
            values = {name: row[index].strip() for name, index in selected.items() if row[index].strip()}
            yield year, open_id, values


def classify_orphan(year: int, base_years: set[int]) -> str:
    if not base_years:
        return "open_id_absent_all_years"
    if year < min(base_years):
        return "before_base_first_seen"
    if year > max(base_years):
        return "after_base_last_seen"
    return "internal_base_gap"


def diagnose(catalog_path: Path, base_code: str = "0101") -> tuple[dict, list[dict]]:
    with catalog_path.open(encoding="utf-8-sig", newline="") as handle:
        catalog = list(csv.DictReader(handle))
    base_item = next((item for item in catalog if item["catalog_code"] == base_code), None)
    if base_item is None:
        raise RuntimeError(f"base catalog code not found: {base_code}")

    base_path = Path(base_item["output_path"])
    base_keys: set[tuple[str, str]] = set()
    base_id_years: dict[str, set[int]] = defaultdict(set)
    base_attributes: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    base_key_rows: Counter[tuple[str, str]] = Counter()
    base_natural_key_rows: Counter[tuple[str, str, str]] = Counter()
    for year, open_id, values in panel_rows(base_path, BASE_ATTRIBUTE_FIELDS):
        if not year or not open_id:
            continue
        key = (year, open_id)
        base_keys.add(key)
        base_key_rows[key] += 1
        base_natural_key_rows[(year, open_id, values.get("본분교명", ""))] += 1
        base_id_years[open_id].add(int(year))
        for field, value in values.items():
            base_attributes[open_id][field].add(value)

    orphan_map: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "datasets": set(),
            "dataset_rows": Counter(),
            "school_types": set(),
        }
    )
    dataset_summaries = []
    for item in catalog:
        if item["catalog_code"] == base_code:
            continue
        path = Path(item["output_path"])
        label = f"{item['catalog_code']} {item['dataset']}"
        orphan_rows: Counter[tuple[str, str]] = Counter()
        school_types: dict[tuple[str, str], set[str]] = defaultdict(set)
        for year, open_id, values in panel_rows(path, ("학교구분명",)):
            if not year or not open_id:
                continue
            key = (year, open_id)
            if key in base_keys:
                continue
            orphan_rows[key] += 1
            school_type = values.get("학교구분명")
            if school_type:
                school_types[key].add(school_type)
        for key, row_count in orphan_rows.items():
            orphan_map[key]["datasets"].add(label)
            orphan_map[key]["dataset_rows"][label] += row_count
            orphan_map[key]["school_types"].update(school_types[key])
        dataset_summaries.append(
            {
                "catalog_code": item["catalog_code"],
                "dataset": item["dataset"],
                "access_tier": item["access_tier"],
                "orphan_key_count": len(orphan_rows),
                "affected_row_count": sum(orphan_rows.values()),
            }
        )

    records = []
    classification_key_counts: Counter[str] = Counter()
    classification_occurrence_counts: Counter[str] = Counter()
    classification_row_counts: Counter[str] = Counter()
    year_key_counts: Counter[str] = Counter()
    for (year_text, open_id), aggregate in sorted(orphan_map.items()):
        year = int(year_text)
        known_years = base_id_years.get(open_id, set())
        classification = classify_orphan(year, known_years)
        datasets = sorted(aggregate["datasets"])
        affected_rows = sum(aggregate["dataset_rows"].values())
        previous_years = [known_year for known_year in known_years if known_year < year]
        next_years = [known_year for known_year in known_years if known_year > year]
        record = {
            "year": year_text,
            "open_id": open_id,
            "classification": classification,
            "base_first_year": str(min(known_years)) if known_years else "",
            "base_last_year": str(max(known_years)) if known_years else "",
            "nearest_base_year_before": str(max(previous_years)) if previous_years else "",
            "nearest_base_year_after": str(min(next_years)) if next_years else "",
            "base_school_types": "|".join(sorted(base_attributes[open_id].get("학교구분명", set()))),
            "base_regions": "|".join(sorted(base_attributes[open_id].get("시도명", set()))),
            "base_branch_types": "|".join(sorted(base_attributes[open_id].get("본분교명", set()))),
            "orphan_school_types": "|".join(sorted(aggregate["school_types"])),
            "dataset_count": len(datasets),
            "datasets": "|".join(datasets),
            "dataset_orphan_key_occurrences": len(datasets),
            "affected_row_count": affected_rows,
        }
        records.append(record)
        classification_key_counts[classification] += 1
        classification_occurrence_counts[classification] += len(datasets)
        classification_row_counts[classification] += affected_rows
        year_key_counts[year_text] += 1

    repeated_base_keys = [count for count in base_key_rows.values() if count > 1]
    repeated_natural_keys = [count for count in base_natural_key_rows.values() if count > 1]
    summary = {
        "generated_at": utc_now(),
        "status": "review_required" if records else "pass",
        "base_dataset": f"{base_code} {base_item['dataset']}",
        "base_school_year_key_count": len(base_keys),
        "base_repeated_school_year_key_count": len(repeated_base_keys),
        "base_duplicate_extra_row_count": sum(count - 1 for count in repeated_base_keys),
        "base_max_key_multiplicity": max(base_key_rows.values(), default=0),
        "base_natural_key": ["_panel_year", "개방ID", "본분교명"],
        "base_natural_key_count": len(base_natural_key_rows),
        "base_repeated_natural_key_count": len(repeated_natural_keys),
        "base_natural_key_extra_row_count": sum(count - 1 for count in repeated_natural_keys),
        "dataset_orphan_key_occurrence_count": sum(item["orphan_key_count"] for item in dataset_summaries),
        "distinct_orphan_school_year_key_count": len(records),
        "distinct_orphan_open_id_count": len({record["open_id"] for record in records}),
        "affected_row_count": sum(record["affected_row_count"] for record in records),
        "classification_key_counts": dict(sorted(classification_key_counts.items())),
        "classification_dataset_occurrence_counts": dict(sorted(classification_occurrence_counts.items())),
        "classification_affected_row_counts": dict(sorted(classification_row_counts.items())),
        "year_key_counts": dict(sorted(year_key_counts.items())),
        "datasets": dataset_summaries,
        "safe_join_rule": "The 0101 natural key is (_panel_year, 개방ID, 본분교명). Other panels usually lack 본분교명, so do not join them directly to raw 0101 rows. Do not delete or force-map orphan keys; first define an explicit aggregation or a validated school-name/status crosswalk.",
        "interpretation_limit": "Most non-0101 panels do not contain school names. Temporal classifications are evidence about coverage timing, not proof of opening, closure, merger, or ID change.",
    }
    return summary, records


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def atomic_write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(records[0]) if records else ["year", "open_id", "classification"]
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/metadata/edss_panel_catalog.csv"))
    parser.add_argument("--base-code", default="0101")
    parser.add_argument("--output-json", type=Path, default=Path("data/metadata/edss_orphan_key_diagnosis.json"))
    parser.add_argument("--output-csv", type=Path, default=Path("data/metadata/edss_orphan_school_year_keys.csv"))
    args = parser.parse_args()
    summary, records = diagnose(args.catalog, args.base_code)
    atomic_write_json(args.output_json, summary)
    atomic_write_csv(args.output_csv, records)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
