#!/usr/bin/env python3
"""Stream-scan every archive in the canonical EDSS full-rebuild inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import build_edss_dataset as builder


def advertised_years(value: str) -> set[str]:
    result: set[str] = set()
    for segment in re.split(r"[,，;]", value):
        years = [int(year) for year in re.findall(r"(?:19|20)\d{2}", segment)]
        if len(years) == 1:
            result.add(str(years[0]))
        elif len(years) >= 2:
            result.update(str(year) for year in range(min(years), max(years) + 1))
    return result


def header_hash(fields: list[str]) -> str:
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


def compact_profile(profile: dict) -> dict:
    variants: dict[tuple[str, ...], dict] = {}
    compact_members = []
    observed_years: set[str] = set()
    for member in profile["members"]:
        fields = tuple(member["original_fields"])
        variant = variants.setdefault(
            fields,
            {
                "header_sha256": header_hash(list(fields)),
                "column_count": len(fields),
                "original_fields": list(fields),
                "member_count": 0,
                "members": [],
            },
        )
        member_ref = f"{member['archive_path']}!{member['member_path']}"
        variant["member_count"] += 1
        variant["members"].append(member_ref)
        observed_years.update(member["observed_years"])
        compact_members.append({key: value for key, value in member.items() if key != "original_fields"})

    expected_years = advertised_years(profile["advertised_years"])
    missing_years = sorted(expected_years - observed_years)
    unexpected_years = sorted(observed_years - expected_years)
    malformed_members = [
        {
            "archive_path": member["archive_path"],
            "member_path": member["member_path"],
            "malformed_row_count": member["malformed_row_count"],
        }
        for member in profile["members"]
        if member["malformed_row_count"]
    ]
    unknown_year_members = [
        f"{member['archive_path']}!{member['member_path']}"
        for member in profile["members"]
        if not member["observed_years"]
    ]
    fields = profile["original_fields"]
    return {
        "generated_at": profile["generated_at"],
        "source": profile["source"],
        "catalog_code": profile["catalog_code"],
        "dataset": profile["dataset"],
        "domn_code": profile["domn_code"],
        "advertised_years": profile["advertised_years"],
        "observed_years": sorted(observed_years),
        "missing_advertised_years": missing_years,
        "unexpected_observed_years": unexpected_years,
        "archive_count": profile["archive_count"],
        "member_count": profile["member_count"],
        "total_rows": profile["total_rows"],
        "malformed_row_count": sum(member["malformed_row_count"] for member in profile["members"]),
        "header_variant_count": profile["header_variant_count"],
        "original_column_count": len(fields),
        "original_fields": fields,
        "field_years": profile["field_years"],
        "has_open_id": "개방ID" in fields,
        "candidate_id_fields": [field for field in fields if field.endswith("ID") or "코드" in field],
        "unknown_year_members": unknown_year_members,
        "malformed_members": malformed_members,
        "header_variants": list(variants.values()),
        "members": compact_members,
        "archive_records": profile["archive_records"],
    }


def scan_entries(
    entries: list[dict],
    raw_root: Path,
    repo_root: Path,
    *,
    progress_every: int = 10,
) -> tuple[list[dict], dict]:
    profiles: list[dict] = []
    issues: list[dict] = []

    def add_issue(severity: str, kind: str, message: str, entry: dict, **context: object) -> None:
        issues.append(
            {
                "severity": severity,
                "type": kind,
                "message": message,
                "source": entry.get("source", ""),
                "catalog_code": entry.get("catalog_code", ""),
                "dataset": entry.get("dataset", ""),
                "domn_code": entry.get("domn_code", ""),
                **context,
            }
        )

    for index, entry in enumerate(entries, start=1):
        try:
            archives = builder.discover_archives(raw_root, entry)
            if not archives:
                raise RuntimeError("no archives resolved from inventory")
            profile = compact_profile(builder.scan_physical_entry(entry, archives, display_root=repo_root))
            profiles.append(profile)
            if profile["member_count"] == 0:
                add_issue("critical", "no_csv_members", "Archive unit contains no CSV members", entry)
            if profile["original_column_count"] == 0:
                add_issue("critical", "empty_schema", "Archive unit has no usable header fields", entry)
            if profile["malformed_row_count"]:
                add_issue(
                    "critical",
                    "malformed_csv_rows",
                    "CSV rows do not match their header width",
                    entry,
                    malformed_row_count=profile["malformed_row_count"],
                    members=profile["malformed_members"],
                )
            if profile["unknown_year_members"]:
                add_issue(
                    "high",
                    "unknown_member_year",
                    "CSV members have no observed or inferable year",
                    entry,
                    members=profile["unknown_year_members"],
                )
            if profile["missing_advertised_years"] or profile["unexpected_observed_years"]:
                add_issue(
                    "medium",
                    "advertised_observed_year_difference",
                    "Observed row years differ from the advertised year range",
                    entry,
                    advertised_years=entry.get("advertised_years", ""),
                    observed_years=profile["observed_years"],
                    missing_advertised_years=profile["missing_advertised_years"],
                    unexpected_observed_years=profile["unexpected_observed_years"],
                )
        except Exception as error:  # Preserve table context and continue the full audit.
            add_issue(
                "critical",
                "scan_exception",
                "Physical archive unit could not be scanned",
                entry,
                error_type=type(error).__name__,
                error_message=str(error),
                archive_paths=entry.get("_inventory_archive_paths", []),
            )
        if progress_every and (index % progress_every == 0 or index == len(entries)):
            print(f"scanned {index}/{len(entries)} physical units", file=sys.stderr, flush=True)

    logical_groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for profile in profiles:
        logical_groups[(profile["source"], profile["catalog_code"], profile["dataset"])].append(profile)

    by_source = {}
    for source in sorted({entry["source"] for entry in entries}):
        source_profiles = [profile for profile in profiles if profile["source"] == source]
        source_entries = [entry for entry in entries if entry["source"] == source]
        by_source[source] = {
            "logical_table_count": len({(item["source"], item["catalog_code"], item["dataset"]) for item in source_entries}),
            "physical_unit_count": len(source_entries),
            "scanned_physical_unit_count": len(source_profiles),
            "archive_count": sum(item["archive_count"] for item in source_profiles),
            "csv_member_count": sum(item["member_count"] for item in source_profiles),
            "row_count": sum(item["total_rows"] for item in source_profiles),
        }

    logical_schema_variant_count = 0
    for group in logical_groups.values():
        signatures = {tuple(profile["original_fields"]) for profile in group}
        if len(signatures) > 1 or any(profile["header_variant_count"] > 1 for profile in group):
            logical_schema_variant_count += 1

    severity_counts = Counter(item["severity"] for item in issues)
    type_counts = Counter(item["type"] for item in issues)
    critical_or_high = severity_counts["critical"] + severity_counts["high"]
    summary = {
        "generated_at": builder.utc_now(),
        "grain": "one profile per physical EDSS domn_code; logical tables group by source, catalog_code, and dataset",
        "logical_table_count": len({(entry["source"], entry["catalog_code"], entry["dataset"]) for entry in entries}),
        "physical_unit_count": len(entries),
        "scanned_physical_unit_count": len(profiles),
        "archive_count": sum(profile["archive_count"] for profile in profiles),
        "csv_member_count": sum(profile["member_count"] for profile in profiles),
        "row_count": sum(profile["total_rows"] for profile in profiles),
        "malformed_row_count": sum(profile["malformed_row_count"] for profile in profiles),
        "physical_units_with_multiple_header_variants": sum(profile["header_variant_count"] > 1 for profile in profiles),
        "logical_tables_with_schema_variants": logical_schema_variant_count,
        "physical_units_with_open_id": sum(profile["has_open_id"] for profile in profiles),
        "logical_tables_with_open_id": len(
            {
                (profile["source"], profile["catalog_code"], profile["dataset"])
                for profile in profiles
                if profile["has_open_id"]
            }
        ),
        "physical_units_with_year_scope_difference": sum(
            bool(profile["missing_advertised_years"] or profile["unexpected_observed_years"])
            for profile in profiles
        ),
        "by_source": by_source,
        "verification": {
            "issue_count": len(issues),
            "issue_counts_by_severity": dict(sorted(severity_counts.items())),
            "issue_counts_by_type": dict(sorted(type_counts.items())),
            "scan_complete": len(profiles) == len(entries),
            "panel_build_ready": len(profiles) == len(entries) and critical_or_high == 0,
        },
        "issues": issues,
    }
    return profiles, summary


def atomic_write_jsonl(path: Path, profiles: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8") as handle:
        for profile in profiles:
            handle.write(json.dumps(profile, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=Path("data/metadata/edss_full_rebuild_inventory.csv"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/edss"))
    parser.add_argument("--profiles", type=Path, default=Path("data/metadata/edss_full_rebuild_schema_scan.jsonl"))
    parser.add_argument("--summary", type=Path, default=Path("data/metadata/edss_full_rebuild_schema_scan_summary.json"))
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    entries = builder.load_rebuild_inventory(args.inventory, args.repo_root)
    profiles, summary = scan_entries(entries, args.raw_root, args.repo_root, progress_every=args.progress_every)
    atomic_write_jsonl(args.profiles, profiles)
    builder.write_json(args.summary, summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "issues"}, ensure_ascii=False))
    return 0 if summary["verification"]["panel_build_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
