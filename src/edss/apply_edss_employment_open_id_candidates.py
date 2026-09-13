#!/usr/bin/env python3
"""Apply reviewed AcademyInfo OpenID candidates to the safe employment panel.

The restricted source panel is never modified.  Only the privacy-safe 2023–2024
school/department derivative receives a canonical ``개방ID`` column.  Existing
non-empty IDs are preserved, conflicts fail closed, and every applied value keeps
explicit inferred-crosswalk provenance.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


CANDIDATE_KEY_FIELDS = ("_panel_year", "_school_identity_key")
IDENTITY_FIELDS = ("학교명", "본분교명", "시도명", "학교종류명")
REQUIRED_CANDIDATE_FIELDS = {
    *CANDIDATE_KEY_FIELDS,
    *IDENTITY_FIELDS,
    "candidate_open_id",
    "candidate_method",
    "resolution_status",
}
REQUIRED_SOURCE_FIELDS = {
    *CANDIDATE_KEY_FIELDS,
    *IDENTITY_FIELDS,
    "_source_row_id",
}
ACCEPTED_CANDIDATE_STATUSES = {
    "candidate_two_year_exact_enrollment",
    "candidate_two_year_exact_enrollment_department_signature_confirmed",
}
ACCEPTED_CANDIDATE_METHOD = "academyinfo_two_year_exact_enrollment_school_context"
APPLICATION_FIELDS = (
    "_open_id_resolution_method",
    "_open_id_resolution_status",
    "_open_id_resolution_source",
)
APPLICATION_METHOD = "approved_academyinfo_two_year_exact_enrollment"
APPLICATION_STATUS = "applied_reviewed_inferred_crosswalk"
APPLICATION_SOURCE = "data/metadata/edss_employment_enrollment_open_id_candidates.csv"
UNRESOLVED_STATUS = "not_applied_no_reviewed_candidate"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, object]:
    return {
        "relative_path": path.as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def read_candidates(path: Path) -> tuple[dict[tuple[str, str], dict[str, str]], dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError(f"candidate file missing required fields: {missing}")
        rows = list(reader)

    lookup: dict[tuple[str, str], dict[str, str]] = {}
    reverse_keys: set[tuple[str, str]] = set()
    status_counts = Counter()
    for row in rows:
        key = tuple(row[field].strip() for field in CANDIDATE_KEY_FIELDS)
        if not all(key):
            raise RuntimeError(f"blank candidate key: {key}")
        if key in lookup:
            raise RuntimeError(f"duplicate candidate key: {key}")
        open_id = row["candidate_open_id"].strip()
        if not re.fullmatch(r"\d{10}", open_id):
            raise RuntimeError(f"invalid candidate OpenID for {key}: {open_id!r}")
        status = row["resolution_status"].strip()
        if status not in ACCEPTED_CANDIDATE_STATUSES:
            raise RuntimeError(f"unapproved candidate status for {key}: {status!r}")
        method = row["candidate_method"].strip()
        if method != ACCEPTED_CANDIDATE_METHOD:
            raise RuntimeError(f"unapproved candidate method for {key}: {method!r}")
        reverse_key = (key[0], open_id)
        if reverse_key in reverse_keys:
            raise RuntimeError(f"candidate OpenID is not reverse-unique within year: {reverse_key}")
        reverse_keys.add(reverse_key)
        lookup[key] = row
        status_counts[status] += 1

    return lookup, {
        "candidate_school_year_count": len(rows),
        "candidate_distinct_open_id_count": len({row["candidate_open_id"].strip() for row in rows}),
        "candidate_status_counts": dict(sorted(status_counts.items())),
        "candidate_key_duplicate_count": 0,
        "candidate_reverse_duplicate_count": 0,
    }


def output_fields(source_fields: list[str]) -> list[str]:
    fields = [field for field in source_fields if field not in {"개방ID", *APPLICATION_FIELDS}]
    if "_panel_year" not in fields:
        raise RuntimeError("source is missing _panel_year")
    year_index = fields.index("_panel_year")
    fields.insert(year_index + 1, "개방ID")
    fields.extend(APPLICATION_FIELDS)
    return fields


def apply_candidates(
    source_path: Path,
    candidate_lookup: dict[tuple[str, str], dict[str, str]],
) -> tuple[list[dict[str, str]], list[str], dict[str, object]]:
    rows: list[dict[str, str]] = []
    seen_source_row_ids: set[str] = set()
    matched_candidate_keys: set[tuple[str, str]] = set()
    applied_open_ids: set[str] = set()
    source_identity_keys: set[tuple[str, str]] = set()
    applied_identity_keys: set[tuple[str, str]] = set()
    applied_rows_by_year = Counter()
    applied_identities_by_year = Counter()
    source_nonempty_open_id_count = 0
    candidate_confirmed_existing_row_count = 0
    newly_imputed_row_count = 0
    remaining_missing_open_id_row_count = 0
    conflict_count = 0

    with gzip.open(source_path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        source_fields = list(reader.fieldnames or [])
        missing = sorted(REQUIRED_SOURCE_FIELDS - set(source_fields))
        if missing:
            raise RuntimeError(f"source panel missing required fields: {missing}")
        fields = output_fields(source_fields)
        for row in reader:
            source_row_id = row["_source_row_id"].strip()
            if not source_row_id:
                raise RuntimeError("source row has blank _source_row_id")
            if source_row_id in seen_source_row_ids:
                raise RuntimeError(f"duplicate source row ID: {source_row_id}")
            seen_source_row_ids.add(source_row_id)

            key = tuple(row[field].strip() for field in CANDIDATE_KEY_FIELDS)
            if not all(key):
                raise RuntimeError(f"blank source identity key: {key}")
            source_identity_keys.add(key)
            candidate = candidate_lookup.get(key)
            existing_open_id = row.get("개방ID", "").strip()
            if existing_open_id:
                source_nonempty_open_id_count += 1

            if candidate is not None:
                for field in IDENTITY_FIELDS:
                    if row[field].strip() != candidate[field].strip():
                        raise RuntimeError(
                            f"identity mismatch for {key} field {field}: "
                            f"{row[field]!r} != {candidate[field]!r}"
                        )
                candidate_open_id = candidate["candidate_open_id"].strip()
                if existing_open_id and existing_open_id != candidate_open_id:
                    conflict_count += 1
                    raise RuntimeError(
                        f"refusing to overwrite conflicting OpenID for {key}: "
                        f"{existing_open_id} != {candidate_open_id}"
                    )
                row["개방ID"] = candidate_open_id
                row["_open_id_resolution_method"] = APPLICATION_METHOD
                row["_open_id_resolution_status"] = APPLICATION_STATUS
                row["_open_id_resolution_source"] = APPLICATION_SOURCE
                if existing_open_id:
                    candidate_confirmed_existing_row_count += 1
                else:
                    newly_imputed_row_count += 1
                matched_candidate_keys.add(key)
                applied_identity_keys.add(key)
                applied_open_ids.add(candidate_open_id)
                applied_rows_by_year[key[0]] += 1
            else:
                row["개방ID"] = existing_open_id
                row["_open_id_resolution_method"] = ""
                row["_open_id_resolution_status"] = UNRESOLVED_STATUS if not existing_open_id else "source_preserved"
                row["_open_id_resolution_source"] = ""
            if not row["개방ID"]:
                remaining_missing_open_id_row_count += 1
            rows.append({field: row.get(field, "") for field in fields})

    unmatched_candidate_keys = sorted(set(candidate_lookup) - matched_candidate_keys)
    if unmatched_candidate_keys:
        raise RuntimeError(f"candidate keys absent from source panel: {unmatched_candidate_keys[:5]}")
    for year, _identity_key in applied_identity_keys:
        applied_identities_by_year[year] += 1

    applied_row_count = sum(applied_rows_by_year.values())
    summary = {
        "source_row_count": len(rows),
        "output_row_count": len(rows),
        "source_column_count": len(source_fields),
        "output_column_count": len(fields),
        "source_school_year_identity_count": len(source_identity_keys),
        "applied_school_year_identity_count": len(applied_identity_keys),
        "remaining_unresolved_school_year_identity_count": len(source_identity_keys - applied_identity_keys),
        "applied_row_count": applied_row_count,
        "newly_imputed_row_count": newly_imputed_row_count,
        "candidate_confirmed_existing_row_count": candidate_confirmed_existing_row_count,
        "remaining_missing_open_id_row_count": remaining_missing_open_id_row_count,
        "applied_row_rate": applied_row_count / len(rows) if rows else 0.0,
        "applied_distinct_open_id_count": len(applied_open_ids),
        "applied_rows_by_year": dict(sorted(applied_rows_by_year.items())),
        "applied_identities_by_year": dict(sorted(applied_identities_by_year.items())),
        "source_nonempty_open_id_row_count": source_nonempty_open_id_count,
        "overwritten_nonempty_open_id_row_count": 0,
        "conflict_count": conflict_count,
        "unmatched_candidate_key_count": 0,
        "source_row_id_duplicate_count": 0,
    }
    return rows, fields, summary


def atomic_write_gzip_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as gzip_handle:
            with io.TextIOWrapper(gzip_handle, encoding="utf-8", newline="") as text_handle:
                writer = csv.DictWriter(text_handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
    temporary.replace(path)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/processed/edss/derived/employment_2023_2024_school_department.csv.gz"),
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=Path("data/metadata/edss_employment_enrollment_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/edss/derived/employment_2023_2024_school_department_resolved.csv.gz"),
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=Path("data/metadata/edss_employment_open_id_application.json"),
    )
    parser.add_argument(
        "--approve-inferred-crosswalk",
        action="store_true",
        help="Required acknowledgement that reviewed inferred candidates, not an official crosswalk, will be applied.",
    )
    args = parser.parse_args()
    if not args.approve_inferred_crosswalk:
        parser.error("--approve-inferred-crosswalk is required")

    candidate_lookup, candidate_summary = read_candidates(args.candidates)
    rows, fields, application_summary = apply_candidates(args.source, candidate_lookup)
    atomic_write_gzip_csv(args.output, rows, fields)

    audit = {
        "status": "applied_reviewed_inferred_crosswalk",
        "generated_at": utc_now(),
        "scope": {
            "years": ["2023", "2024"],
            "source_grain": "one row per employment school/department aggregate record",
            "output_grain": "unchanged from source",
            "restricted_source_modified": False,
            "official_crosswalk": False,
        },
        "inputs": {
            "safe_derived_employment": file_record(args.source),
            "reviewed_candidates": file_record(args.candidates),
        },
        "candidates": candidate_summary,
        "application": application_summary,
        "output": {
            **file_record(args.output),
            "row_count": len(rows),
            "column_count": len(fields),
        },
        "safety": {
            "existing_nonempty_open_ids_preserved": True,
            "conflicts_fail_closed": True,
            "unmatched_rows_preserved": True,
            "provenance_columns_added": list(APPLICATION_FIELDS),
            "interpretation": "Applied IDs are reviewed inferred crosswalk values and are not represented as an official EDSS crosswalk.",
        },
    }
    atomic_write_json(args.audit_output, audit)
    print(
        json.dumps(
            {
                "status": audit["status"],
                "output_rows": application_summary["output_row_count"],
                "applied_rows": application_summary["applied_row_count"],
                "remaining_missing_rows": application_summary["remaining_missing_open_id_row_count"],
                "applied_school_year_identities": application_summary["applied_school_year_identity_count"],
                "applied_distinct_open_ids": application_summary["applied_distinct_open_id_count"],
                "conflicts": application_summary["conflict_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
