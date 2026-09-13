#!/usr/bin/env python3
"""Build noncanonical EDSS OpenID candidates from AcademyInfo enrollment.

A candidate is emitted only when the same AcademyInfo school ID has numeric
2023 and 2024 enrollment values, each value matches exactly one EDSS 0101
OpenID in the same school-division, region, and branch context, and both years
select the same OpenID. The script never writes a canonical ``개방ID`` column.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


YEARS = ("2023", "2024")
PUBLIC_CANDIDATE_FIELDS = (
    "academyinfo_school_id",
    "school_name_2023",
    "school_name_2024",
    "school_full_name_2023",
    "school_full_name_2024",
    "school_division_2023",
    "school_division_2024",
    "school_kind_2023",
    "school_kind_2024",
    "branch_2023",
    "branch_2024",
    "region_2023",
    "region_2024",
    "academyinfo_enrollment_2023",
    "academyinfo_enrollment_2024",
    "edss_exact_open_id_count_2023",
    "edss_exact_open_id_count_2024",
    "edss_exact_open_ids_2023",
    "edss_exact_open_ids_2024",
    "candidate_open_id",
    "candidate_method",
    "resolution_status",
)
EMPLOYMENT_MATCH_FIELDS = (
    "_panel_year",
    "_school_identity_key",
    "학교명",
    "본분교명",
    "시도명",
    "학교종류명",
    "academyinfo_school_id",
    "academyinfo_school_name",
    "academyinfo_enrollment",
    "edss_0101_enrollment",
    "candidate_open_id",
    "candidate_method",
    "department_signature_candidate_open_id",
    "department_signature_resolution_status",
    "cross_validation_status",
    "resolution_status",
)
NAME_ALIASES = {
    "건국대학교글로컬": "건국대학교",
    "고려대학교세종": "고려대학교세종캠퍼스",
    "동국대학교wise": "동국대학교",
    "연세대학교미래": "연세대학교미래캠퍼스",
    "한양대학교erica": "한양대학교",
    "가야대학교김해": "가야대학교",
    "한국골프과학기술대학교": "한국골프대학교",
    "재능대학교": "인천재능대학교",
    "영산대학교해운대": "영산대학교",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^0-9a-z가-힣]", "", normalized)


def canonical_school_name(value: str) -> str:
    normalized = normalize_text(value)
    if normalized.startswith("국립"):
        normalized = normalized[2:]
    return NAME_ALIASES.get(normalized, normalized)


def normalize_branch(value: str) -> str:
    normalized = normalize_text(value)
    if normalized == "본교":
        return "본교"
    if normalized in {"분교", "제2캠퍼스"}:
        return "분교"
    return normalized


def compatible_provinces(public_region: str) -> set[str]:
    normalized = normalize_text(public_region)
    if normalized == "전남광주":
        return {"전남", "광주"}
    return {normalized}


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


def write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    with partial.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(partial, path)


def load_public(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["schlId"], row["svyYr"])
        if key in lookup:
            raise RuntimeError(f"duplicate AcademyInfo school-year key: {key}")
        lookup[key] = row
    return lookup


def build_edss_index(path: Path) -> dict[tuple[str, str, str, str, str], set[str]]:
    exact_index: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "_panel_year",
            "개방ID",
            "학교구분명",
            "시도명",
            "본분교명",
            "고등교육학교_재적학생수",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"EDSS 0101 is missing fields: {sorted(missing)}")
        for row in reader:
            year = row["_panel_year"]
            if year not in YEARS:
                continue
            open_id = row["개방ID"].strip()
            enrollment = row["고등교육학교_재적학생수"].strip()
            if not open_id or not enrollment.isdigit():
                continue
            key = (
                year,
                normalize_text(row["학교구분명"]),
                normalize_text(row["시도명"]),
                normalize_branch(row["본분교명"]),
                enrollment,
            )
            exact_index[key].add(open_id)
    return exact_index


def exact_open_ids(public_row: dict[str, str], exact_index: dict) -> set[str]:
    year = public_row["svyYr"]
    enrollment = public_row["indctVal1"].strip()
    if year not in YEARS or not enrollment.isdigit():
        return set()
    matches: set[str] = set()
    for province in compatible_provinces(public_row["znNm"]):
        key = (
            year,
            normalize_text(public_row["schlDivNm"]),
            province,
            normalize_branch(public_row["clgcpDivNm"]),
            enrollment,
        )
        matches.update(exact_index.get(key, set()))
    return matches


def public_resolution(
    school_id: str,
    public_lookup: dict[tuple[str, str], dict[str, str]],
    exact_index: dict,
) -> dict[str, str]:
    rows = {year: public_lookup.get((school_id, year), {}) for year in YEARS}
    matches = {year: exact_open_ids(rows[year], exact_index) if rows[year] else set() for year in YEARS}
    numeric = {year: bool(rows[year]) and rows[year].get("indctVal1", "").isdigit() for year in YEARS}
    candidate = ""
    method = ""
    if not all(numeric.values()):
        status = "unresolved_missing_two_year_numeric_enrollment"
    elif not all(len(matches[year]) == 1 for year in YEARS):
        status = "unresolved_two_year_exact_match_not_unique"
    else:
        values = {next(iter(matches[year])) for year in YEARS}
        if len(values) != 1:
            status = "conflict_two_year_exact_open_id_changed"
        else:
            candidate = next(iter(values))
            method = "academyinfo_two_year_exact_enrollment_school_context"
            status = "candidate_two_year_exact_enrollment"
    output = {
        "academyinfo_school_id": school_id,
        "candidate_open_id": candidate,
        "candidate_method": method,
        "resolution_status": status,
    }
    for year in YEARS:
        row = rows[year]
        output.update(
            {
                f"school_name_{year}": row.get("schlKrnNm", ""),
                f"school_full_name_{year}": row.get("schlFullNm", ""),
                f"school_division_{year}": row.get("schlDivNm", ""),
                f"school_kind_{year}": row.get("schlKndNm", ""),
                f"branch_{year}": row.get("clgcpDivNm", ""),
                f"region_{year}": row.get("znNm", ""),
                f"academyinfo_enrollment_{year}": row.get("indctVal1", ""),
                f"edss_exact_open_id_count_{year}": str(len(matches[year])),
                f"edss_exact_open_ids_{year}": "|".join(sorted(matches[year])),
            }
        )
    return output


def enforce_reverse_unique(rows: list[dict[str, str]]) -> int:
    """Reject candidates when more than one public school selects an OpenID."""
    counts = Counter(row["candidate_open_id"] for row in rows if row["candidate_open_id"])
    duplicated = {open_id for open_id, count in counts.items() if count > 1}
    for row in rows:
        if row["candidate_open_id"] in duplicated:
            row["candidate_open_id"] = ""
            row["candidate_method"] = ""
            row["resolution_status"] = "conflict_reverse_open_id_not_unique"
    return len(duplicated)


def employment_index(path: Path) -> tuple[dict[tuple[str, str, str], list[dict[str, str]]], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    lookup: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            row["_panel_year"],
            canonical_school_name(row["학교명"]),
            normalize_branch(row["본분교명"]),
        )
        lookup[key].append(row)
    return lookup, rows


def expected_employment_school_kind(public_row: dict[str, str]) -> str:
    if normalize_text(public_row.get("schlKndNm", "")) == "교육대학":
        return "교육대학"
    if normalize_text(public_row.get("schlDivNm", "")) == "전문대학":
        return "전문대학"
    return "대학"


def match_employment(
    public_candidates: list[dict[str, str]],
    public_lookup: dict[tuple[str, str], dict[str, str]],
    employment_lookup: dict,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    output: list[dict[str, str]] = []
    diagnostics = Counter()
    for candidate in public_candidates:
        if candidate["resolution_status"] != "candidate_two_year_exact_enrollment":
            continue
        school_id = candidate["academyinfo_school_id"]
        open_id = candidate["candidate_open_id"]
        for year in YEARS:
            public_row = public_lookup[(school_id, year)]
            key = (
                year,
                canonical_school_name(public_row["schlKrnNm"]),
                normalize_branch(public_row["clgcpDivNm"]),
            )
            targets = [
                target
                for target in employment_lookup.get(key, [])
                if normalize_text(target["시도명"]) in compatible_provinces(public_row["znNm"])
                and normalize_text(target["학교종류명"])
                == normalize_text(expected_employment_school_kind(public_row))
            ]
            if len(targets) != 1:
                diagnostics["employment_name_unmatched" if not targets else "employment_name_ambiguous"] += 1
                continue
            target = targets[0]
            previous_candidate = target["candidate_open_id"].strip()
            previous_status = target["resolution_status"]
            if previous_candidate:
                if previous_candidate == open_id:
                    cross_validation = "department_signature_agreement"
                    diagnostics["department_signature_agreement"] += 1
                else:
                    cross_validation = "department_signature_conflict"
                    diagnostics["department_signature_conflict"] += 1
            else:
                cross_validation = "not_available"
            status = (
                "candidate_two_year_exact_enrollment_department_signature_confirmed"
                if cross_validation == "department_signature_agreement"
                else "candidate_two_year_exact_enrollment"
            )
            output.append(
                {
                    "_panel_year": year,
                    "_school_identity_key": target["_school_identity_key"],
                    "학교명": target["학교명"],
                    "본분교명": target["본분교명"],
                    "시도명": target["시도명"],
                    "학교종류명": target["학교종류명"],
                    "academyinfo_school_id": school_id,
                    "academyinfo_school_name": public_row["schlKrnNm"],
                    "academyinfo_enrollment": public_row["indctVal1"],
                    "edss_0101_enrollment": public_row["indctVal1"],
                    "candidate_open_id": open_id,
                    "candidate_method": candidate["candidate_method"],
                    "department_signature_candidate_open_id": previous_candidate,
                    "department_signature_resolution_status": previous_status,
                    "cross_validation_status": cross_validation,
                    "resolution_status": status,
                }
            )
            diagnostics["employment_school_year_match"] += 1
    output.sort(key=lambda row: (row["_panel_year"], row["학교명"], row["본분교명"]))
    return output, dict(diagnostics)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--public-csv",
        type=Path,
        default=Path("data/raw/open_api/academyinfo_enrollment/academyinfo_enrollment_2023_2024.csv"),
    )
    parser.add_argument(
        "--edss-0101",
        type=Path,
        default=Path("data/processed/edss/panel/고등교육통계/0101_고등교육학교개황/panel.csv.gz"),
    )
    parser.add_argument(
        "--employment-candidates",
        type=Path,
        default=Path("data/metadata/edss_employment_2023_2024_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--public-output",
        type=Path,
        default=Path("data/metadata/edss_academyinfo_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--employment-output",
        type=Path,
        default=Path("data/metadata/edss_employment_enrollment_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/metadata/edss_academyinfo_open_id_match.json"),
    )
    args = parser.parse_args()

    public_lookup = load_public(args.public_csv)
    exact_index = build_edss_index(args.edss_0101)
    school_ids = sorted({school_id for school_id, _year in public_lookup})
    public_candidates = [public_resolution(school_id, public_lookup, exact_index) for school_id in school_ids]
    reverse_duplicate_open_id_count = enforce_reverse_unique(public_candidates)
    write_csv(args.public_output, PUBLIC_CANDIDATE_FIELDS, public_candidates)

    employment_lookup, employment_rows = employment_index(args.employment_candidates)
    employment_matches, diagnostics = match_employment(
        public_candidates,
        public_lookup,
        employment_lookup,
    )
    write_csv(args.employment_output, EMPLOYMENT_MATCH_FIELDS, employment_matches)

    public_status_counts = Counter(row["resolution_status"] for row in public_candidates)
    employment_status_counts = Counter(row["resolution_status"] for row in employment_matches)
    year_numeric_counts = {
        year: sum(row.get("indctVal1", "").isdigit() for (school_id, row_year), row in public_lookup.items() if row_year == year)
        for year in YEARS
    }
    exact_unique_school_year_count = sum(
        int(row[f"edss_exact_open_id_count_{year}"]) == 1
        for row in public_candidates
        for year in YEARS
    )
    candidate_count = public_status_counts["candidate_two_year_exact_enrollment"]
    signature_agreement = diagnostics.get("department_signature_agreement", 0)
    signature_conflict = diagnostics.get("department_signature_conflict", 0)
    status = "review_required" if signature_conflict == 0 else "conflict_detected"
    report = {
        "status": status,
        "generated_at": utc_now(),
        "years": list(YEARS),
        "inputs": {
            "academyinfo_csv": {
                "relative_path": args.public_csv.as_posix(),
                "bytes": args.public_csv.stat().st_size,
                "sha256": sha256_file(args.public_csv),
            },
            "edss_0101": {
                "relative_path": args.edss_0101.as_posix(),
                "bytes": args.edss_0101.stat().st_size,
                "sha256": sha256_file(args.edss_0101),
            },
            "employment_candidates": {
                "relative_path": args.employment_candidates.as_posix(),
                "rows": len(employment_rows),
                "sha256": sha256_file(args.employment_candidates),
            },
        },
        "academyinfo": {
            "school_id_count": len(school_ids),
            "numeric_enrollment_school_year_counts": year_numeric_counts,
            "unique_exact_edss_school_year_count": exact_unique_school_year_count,
            "candidate_two_year_exact_enrollment_school_count": candidate_count,
            "candidate_distinct_open_id_count": len(
                {row["candidate_open_id"] for row in public_candidates if row["candidate_open_id"]}
            ),
            "candidate_reverse_duplicate_open_id_count": reverse_duplicate_open_id_count,
            "resolution_status_counts": dict(public_status_counts),
        },
        "employment": {
            "school_year_identity_count": len(employment_rows),
            "matched_candidate_school_year_count": len(employment_matches),
            "unmatched_candidate_school_year_count": candidate_count * len(YEARS) - len(employment_matches),
            "resolution_status_counts": dict(employment_status_counts),
            "department_signature_agreement_count": signature_agreement,
            "department_signature_conflict_count": signature_conflict,
            "diagnostics": diagnostics,
        },
        "safety": {
            "official_crosswalk": False,
            "canonical_open_id_imputed_row_count": 0,
            "candidate_rule": (
                "same AcademyInfo school ID; numeric 2023 and 2024 enrollment; exactly one EDSS 0101 match "
                "per year within school-division, compatible-region, and branch context; same OpenID in both years"
            ),
        },
        "outputs": {
            "public_candidates": {
                "relative_path": args.public_output.as_posix(),
                "rows": len(public_candidates),
                "sha256": sha256_file(args.public_output),
            },
            "employment_candidates": {
                "relative_path": args.employment_output.as_posix(),
                "rows": len(employment_matches),
                "sha256": sha256_file(args.employment_output),
            },
        },
    }
    atomic_write_text(args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
