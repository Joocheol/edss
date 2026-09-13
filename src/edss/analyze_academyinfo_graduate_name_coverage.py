#!/usr/bin/env python3
"""Compare 2025 AcademyInfo graduate-school names with EDSS identity gaps.

This creates review evidence only.  It never writes an EDSS OpenID because the
source API has no graduate-school identifier and covers a later survey year.
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


GRADUATE_KINDS = {"일반대학원", "전문대학원", "특수대학원"}
PROVINCE_ALIASES = {
    "서울특별시": "서울",
    "부산광역시": "부산",
    "대구광역시": "대구",
    "인천광역시": "인천",
    "광주광역시": "광주",
    "대전광역시": "대전",
    "울산광역시": "울산",
    "세종특별자치시": "세종",
    "경기도": "경기",
    "강원특별자치도": "강원",
    "강원도": "강원",
    "충청북도": "충북",
    "충청남도": "충남",
    "전북특별자치도": "전북",
    "전라북도": "전북",
    "전라남도": "전남",
    "경상북도": "경북",
    "경상남도": "경남",
    "제주특별자치도": "제주",
}
OUTPUT_FIELDS = (
    "_panel_year",
    "_school_identity_key",
    "edss_school_name",
    "edss_school_kind",
    "edss_province",
    "edss_source_row_count",
    "edss_department_count",
    "api_survey_year",
    "api_school_name",
    "api_school_kind",
    "api_provinces",
    "api_department_count",
    "department_intersection_count",
    "department_union_count",
    "department_jaccard",
    "name_match_method",
    "match_status",
    "candidate_open_id",
    "canonical_open_id_imputed",
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


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()


def normalize_school_name(value: str, strip_terminal_parenthetical: bool = False) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    if strip_terminal_parenthetical:
        value = re.sub(r"\s*\([^()]*(?:캠퍼스|교|시|군|구|김해|고령|서울|부산|대구|인천|광주|대전|울산|세종|제주)[^()]*\)\s*$", "", value)
    return normalize_text(value)


def normalize_province(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    return PROVINCE_ALIASES.get(value, value)


def read_csv(path: Path) -> list[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def api_school_profiles(rows: list[dict[str, str]]) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        kind = row.get("schlKndNm", "")
        if kind not in GRADUATE_KINDS:
            continue
        key = (row.get("schlNm", ""), kind)
        profile = grouped.setdefault(
            key,
            {
                "survey_year": row.get("svyYr", ""),
                "school_name": row.get("schlNm", ""),
                "school_kind": kind,
                "provinces": set(),
                "departments": set(),
            },
        )
        profile["provinces"].add(normalize_province(row.get("mjrAreaNm", "")))
        profile["departments"].add(normalize_text(row.get("korMjrNm", "")))
    return list(grouped.values())


def edss_department_sets(rows: list[dict[str, str]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("학교종류명") in GRADUATE_KINDS:
            result[row.get("_school_identity_key", "")].add(normalize_text(row.get("학과명", "")))
    return result


def profile_indexes(profiles: list[dict]) -> tuple[dict[tuple[str, str, str], list[dict]], dict[tuple[str, str, str], list[dict]]]:
    exact: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    relaxed: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for profile in profiles:
        for province in profile["provinces"]:
            exact[(normalize_school_name(profile["school_name"]), profile["school_kind"], province)].append(profile)
            relaxed[
                (
                    normalize_school_name(profile["school_name"], strip_terminal_parenthetical=True),
                    profile["school_kind"],
                    province,
                )
            ].append(profile)
    return exact, relaxed


def unique_profiles(values: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for value in values:
        key = (value["school_name"], value["school_kind"])
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def match_identity(
    row: dict[str, str],
    exact_index: dict[tuple[str, str, str], list[dict]],
    relaxed_index: dict[tuple[str, str, str], list[dict]],
    edss_departments: set[str],
) -> dict[str, str]:
    name = row.get("학교명", "")
    kind = row.get("학교종류명", "")
    province = normalize_province(row.get("시도명", ""))
    exact_candidates = unique_profiles(exact_index.get((normalize_school_name(name), kind, province), []))
    method = "exact_normalized_name_kind_region"
    candidates = exact_candidates
    if not candidates:
        candidates = unique_profiles(
            relaxed_index.get((normalize_school_name(name, strip_terminal_parenthetical=True), kind, province), [])
        )
        method = "terminal_parenthetical_normalized_name_kind_region"
    if len(candidates) == 1:
        profile = candidates[0]
        api_departments = {value for value in profile["departments"] if value}
        edss_departments = {value for value in edss_departments if value}
        intersection = len(api_departments & edss_departments)
        union = len(api_departments | edss_departments)
        status = "candidate_unique_name_context"
        return {
            "api_survey_year": profile["survey_year"],
            "api_school_name": profile["school_name"],
            "api_school_kind": profile["school_kind"],
            "api_provinces": "|".join(sorted(profile["provinces"])),
            "api_department_count": str(len(api_departments)),
            "department_intersection_count": str(intersection),
            "department_union_count": str(union),
            "department_jaccard": f"{intersection / union:.6f}" if union else "",
            "name_match_method": method,
            "match_status": status,
        }
    return {
        "api_survey_year": "",
        "api_school_name": "|".join(sorted(profile["school_name"] for profile in candidates)),
        "api_school_kind": kind if candidates else "",
        "api_provinces": province if candidates else "",
        "api_department_count": "",
        "department_intersection_count": "",
        "department_union_count": "",
        "department_jaccard": "",
        "name_match_method": method if candidates else "",
        "match_status": "ambiguous_name_context" if candidates else "unmatched_name_context",
    }


def analyze(
    api_rows: list[dict[str, str]],
    identities: list[dict[str, str]],
    employment_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    profiles = api_school_profiles(api_rows)
    exact_index, relaxed_index = profile_indexes(profiles)
    department_sets = edss_department_sets(employment_rows)
    output = []
    for row in identities:
        if row.get("학교종류명") not in GRADUATE_KINDS:
            continue
        result = {
            "_panel_year": row.get("_panel_year", ""),
            "_school_identity_key": row.get("_school_identity_key", ""),
            "edss_school_name": row.get("학교명", ""),
            "edss_school_kind": row.get("학교종류명", ""),
            "edss_province": normalize_province(row.get("시도명", "")),
            "edss_source_row_count": row.get("source_row_count", ""),
            "edss_department_count": row.get("department_count", ""),
        }
        result.update(
            match_identity(
                row,
                exact_index,
                relaxed_index,
                department_sets.get(row.get("_school_identity_key", ""), set()),
            )
        )
        result["candidate_open_id"] = ""
        result["canonical_open_id_imputed"] = "false"
        output.append(result)
    return output


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
    parser.add_argument(
        "--api",
        type=Path,
        default=Path("data/raw/open_api/academyinfo_school_major/graduate_school_major.csv"),
    )
    parser.add_argument(
        "--identities",
        type=Path,
        default=Path("data/metadata/edss_employment_2023_2024_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--employment",
        type=Path,
        default=Path("data/processed/edss/derived/employment_2023_2024_school_department.csv.gz"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/metadata/edss_academyinfo_graduate_name_candidates.csv"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/metadata/edss_academyinfo_graduate_name_match.json"),
    )
    args = parser.parse_args()

    api_rows = read_csv(args.api)
    identities = read_csv(args.identities)
    employment_rows = read_csv(args.employment)
    rows = analyze(api_rows, identities, employment_rows)
    rows.sort(key=lambda row: (row["_panel_year"], row["edss_school_kind"], row["edss_school_name"]))
    write_csv(args.output, rows)
    statuses = Counter(row["match_status"] for row in rows)
    matched = [row for row in rows if row["match_status"] == "candidate_unique_name_context"]
    positive_overlap = [row for row in matched if int(row["department_intersection_count"] or 0) > 0]
    report = {
        "status": "review_required",
        "generated_at": utc_now(),
        "source_api": "한국대학교육협의회_대학별 학과정보_GW 2025",
        "edss_population": "취업통계 2023-2024 일반·전문·특수대학원 학교-연도 identity",
        "input": {
            "api": {"relative_path": args.api.as_posix(), "sha256": sha256_file(args.api)},
            "identities": {
                "relative_path": args.identities.as_posix(),
                "sha256": sha256_file(args.identities),
            },
            "employment": {
                "relative_path": args.employment.as_posix(),
                "sha256": sha256_file(args.employment),
            },
        },
        "graduate_identity_count": len(rows),
        "source_row_count": sum(int(row["edss_source_row_count"] or 0) for row in rows),
        "match_status_counts": dict(sorted(statuses.items())),
        "unique_name_context_match_count": len(matched),
        "unique_name_context_match_rate": round(len(matched) / len(rows), 6) if rows else 0,
        "positive_department_overlap_count": len(positive_overlap),
        "canonical_open_id_imputed_row_count": 0,
        "output": {
            "relative_path": args.output.as_posix(),
            "row_count": len(rows),
            "bytes": args.output.stat().st_size,
            "sha256": sha256_file(args.output),
        },
        "limitations": [
            "The AcademyInfo source is survey year 2025 while EDSS identities are 2023-2024.",
            "The API has graduate-school names but no graduate-school identifier or EDSS OpenID.",
            "Department overlap is supporting evidence only because departments can open, close, rename, or be reported at different grains.",
            "No candidate or canonical OpenID is populated by this analysis.",
        ],
    }
    atomic_write_text(args.report, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
