#!/usr/bin/env python3
"""Infer review-only EDSS graduate-school OpenID candidates.

The target population is the 2023-2024 employment aggregate, where EDSS
stopped publishing ``개방ID``.  Candidate IDs come from same-year EDSS
university-disclosure panels that still contain normal OpenIDs.  AcademyInfo
2025 department/day-night data and cross-year continuity are corroborating
signals only.  This script never writes a canonical ``개방ID`` column and
never modifies raw or restricted data.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


GRADUATE_KIND_TABLES = {
    "일반대학원": "university_disclosure.panel_0402",
    "전문대학원": "university_disclosure.panel_0403",
    "특수대학원": "university_disclosure.panel_0404",
}
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
    "edss_branch",
    "name_context_status",
    "target_department_count",
    "context_candidate_count",
    "overlap_candidate_count",
    "top_candidate_open_id",
    "candidate_signature_year",
    "candidate_open_id",
    "candidate_method",
    "confidence_tier",
    "resolution_status",
    "score",
    "score_margin",
    "department_intersection_count",
    "department_union_count",
    "department_jaccard",
    "target_department_coverage",
    "candidate_department_coverage",
    "academyinfo_department_jaccard",
    "academyinfo_day_night_jaccard",
    "cross_panel_department_jaccard",
    "cross_year_group_key",
    "cross_year_consistent",
    "year_conflict_excluded",
    "reverse_collision_excluded",
    "evidence_sources",
    "canonical_open_id_imputed",
)


@dataclass(frozen=True)
class ProfileKey:
    year: str
    school_kind: str
    open_id: str


@dataclass
class CandidateProfile:
    departments: set[str]
    department_college_pairs: set[tuple[str, str]]
    disclosure_departments: set[str]
    disclosure_day_night_pairs: set[tuple[str, str]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value).casefold()


def normalize_province(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    return normalize_text(PROVINCE_ALIASES.get(value, value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def split_values(value: str, normalizer=normalize_text) -> set[str]:
    return {normalizer(item) for item in (value or "").split("|") if normalizer(item)}


def jaccard(left: set, right: set) -> float | None:
    union = left | right
    if not union:
        return None
    return len(left & right) / len(union)


def coverage(left: set, right: set) -> float | None:
    if not left:
        return None
    return len(left & right) / len(left)


def weighted_mean(values: Iterable[tuple[float | None, float]]) -> float:
    available = [(value, weight) for value, weight in values if value is not None]
    if not available:
        return 0.0
    return sum(value * weight for value, weight in available) / sum(weight for _value, weight in available)


def fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def load_bridge(path: Path) -> dict[tuple[str, str], dict[str, set[str]]]:
    rows = read_csv(path)
    bridge: dict[tuple[str, str], dict[str, set[str]]] = {}
    for row in rows:
        key = (row["_panel_year"], row["개방ID"])
        if key in bridge:
            raise RuntimeError(f"duplicate bridge key: {key}")
        bridge[key] = {
            "provinces": split_values(row.get("_0101_provinces", ""), normalize_province),
            "branches": split_values(row.get("_0101_branch_names", "")),
            "school_types": split_values(row.get("_0101_school_types", "")),
        }
    return bridge


def load_target_profiles(
    name_candidate_path: Path,
    identity_path: Path,
    employment_path: Path,
) -> tuple[list[dict[str, str]], dict[str, dict[str, set]]]:
    targets = read_csv(name_candidate_path)
    identity_by_key = {row["_school_identity_key"]: row for row in read_csv(identity_path)}
    profiles: dict[str, dict[str, set]] = defaultdict(
        lambda: {"departments": set(), "department_college_pairs": set()}
    )
    for row in read_csv(employment_path):
        key = row.get("_school_identity_key", "")
        department = normalize_text(row.get("학과명", ""))
        college = normalize_text(row.get("단과대학명", ""))
        if department:
            profiles[key]["departments"].add(department)
            profiles[key]["department_college_pairs"].add((department, college))
    for row in targets:
        identity = identity_by_key.get(row["_school_identity_key"], {})
        row["edss_branch"] = identity.get("본분교명", "")
    return targets, profiles


def load_academyinfo_profiles(path: Path) -> dict[tuple[str, str], dict[str, set]]:
    profiles: dict[tuple[str, str], dict[str, set]] = defaultdict(
        lambda: {"departments": set(), "day_night_pairs": set()}
    )
    for row in read_csv(path):
        kind = row.get("schlKndNm", "")
        if kind not in GRADUATE_KIND_TABLES:
            continue
        key = (row.get("schlNm", ""), kind)
        department = normalize_text(row.get("korMjrNm", ""))
        day_night = normalize_text(row.get("dghtDivNm", ""))
        if department:
            profiles[key]["departments"].add(department)
            profiles[key]["day_night_pairs"].add((department, day_night))
    return profiles


def load_candidate_profiles(connection, years: tuple[str, ...]) -> dict[ProfileKey, CandidateProfile]:
    profiles: dict[ProfileKey, CandidateProfile] = {}
    year_sql = ",".join("?" for _ in years)
    for kind, table in GRADUATE_KIND_TABLES.items():
        rows = connection.execute(
            f'''SELECT _panel_year, "개방ID", "학과명", "단과대학명"
                FROM {table}
                WHERE _panel_year IN ({year_sql})
                  AND COALESCE("개방ID", '') <> ''
                  AND COALESCE("학과명", '') <> ''
                GROUP BY ALL''',
            list(years),
        ).fetchall()
        for year, open_id, department, college in rows:
            key = ProfileKey(year, kind, open_id)
            profile = profiles.setdefault(key, CandidateProfile(set(), set(), set(), set()))
            normalized_department = normalize_text(department)
            if normalized_department:
                profile.departments.add(normalized_department)
                profile.department_college_pairs.add(
                    (normalized_department, normalize_text(college))
                )

    rows = connection.execute(
        f'''SELECT _panel_year, "개방ID", "학과한글명", "단과대학명", "주야간계절구분명"
            FROM university_disclosure.panel_0104
            WHERE _panel_year IN ({year_sql})
              AND "학교구분명" IN ('대학원', '대학원대학')
              AND COALESCE("개방ID", '') <> ''
              AND COALESCE("학과한글명", '') <> ''
            GROUP BY ALL''',
        list(years),
    ).fetchall()
    by_year_id: dict[tuple[str, str], list[CandidateProfile]] = defaultdict(list)
    for key, profile in profiles.items():
        by_year_id[(key.year, key.open_id)].append(profile)
    for year, open_id, department, _college, day_night in rows:
        normalized_department = normalize_text(department)
        for profile in by_year_id.get((year, open_id), []):
            profile.disclosure_departments.add(normalized_department)
            profile.disclosure_day_night_pairs.add(
                (normalized_department, normalize_text(day_night))
            )
    return profiles


def context_matches(
    key: ProfileKey,
    province: str,
    branch: str,
    bridge: dict[tuple[str, str], dict[str, set[str]]],
    context_year: str | None = None,
) -> bool:
    context = bridge.get((context_year or key.year, key.open_id))
    if context is None:
        return False
    if province and province not in context["provinces"]:
        return False
    if branch and branch not in context["branches"]:
        return False
    return True


def rank_candidates(
    year: str,
    kind: str,
    province: str,
    branch: str,
    target_departments: set[str],
    academy_profile: dict[str, set] | None,
    profiles: dict[ProfileKey, CandidateProfile],
    bridge: dict[tuple[str, str], dict[str, set[str]]],
) -> list[dict]:
    ranked = []
    latest_profiles: dict[str, tuple[ProfileKey, CandidateProfile]] = {}
    for key, candidate in profiles.items():
        if key.school_kind != kind or key.year > year:
            continue
        previous = latest_profiles.get(key.open_id)
        if previous is None or key.year > previous[0].year:
            latest_profiles[key.open_id] = (key, candidate)
    for key, candidate in latest_profiles.values():
        if not context_matches(key, province, branch, bridge, context_year=year):
            continue
        intersection = len(target_departments & candidate.departments)
        department_jaccard = jaccard(target_departments, candidate.departments)
        target_coverage = coverage(target_departments, candidate.departments)
        candidate_coverage = coverage(candidate.departments, target_departments)
        academy_departments = academy_profile["departments"] if academy_profile else set()
        academy_day_night = academy_profile["day_night_pairs"] if academy_profile else set()
        academy_jaccard = jaccard(academy_departments, candidate.disclosure_departments)
        day_night_jaccard = jaccard(
            academy_day_night,
            candidate.disclosure_day_night_pairs,
        )
        cross_panel_jaccard = jaccard(
            candidate.departments,
            candidate.disclosure_departments,
        )
        score = weighted_mean(
            (
                (department_jaccard, 0.55),
                (target_coverage, 0.15),
                (academy_jaccard, 0.15),
                (day_night_jaccard, 0.05),
                (cross_panel_jaccard, 0.10),
            )
        )
        ranked.append(
            {
                "open_id": key.open_id,
                "signature_year": key.year,
                "score": score,
                "intersection": intersection,
                "union": len(target_departments | candidate.departments),
                "department_jaccard": department_jaccard,
                "target_coverage": target_coverage,
                "candidate_coverage": candidate_coverage,
                "academy_jaccard": academy_jaccard,
                "day_night_jaccard": day_night_jaccard,
                "cross_panel_jaccard": cross_panel_jaccard,
                "exact": bool(target_departments) and target_departments == candidate.departments,
            }
        )
    ranked.sort(
        key=lambda row: (
            row["score"],
            row["intersection"],
            row["target_coverage"] or 0,
            row["candidate_coverage"] or 0,
            row["open_id"],
        ),
        reverse=True,
    )
    return ranked


def calibrate_backtest(
    profiles: dict[ProfileKey, CandidateProfile],
    bridge: dict[tuple[str, str], dict[str, set[str]]],
    year: str = "2022",
) -> dict:
    observations = []
    for truth_key, truth_profile in profiles.items():
        if truth_key.year != year or not truth_profile.disclosure_departments:
            continue
        context = bridge.get((year, truth_key.open_id))
        if not context or not context["provinces"] or not context["branches"]:
            continue
        province = sorted(context["provinces"])[0]
        branch = sorted(context["branches"])[0]
        ranked = rank_candidates(
            year,
            truth_key.school_kind,
            province,
            branch,
            truth_profile.disclosure_departments,
            None,
            profiles,
            bridge,
        )
        if not ranked:
            continue
        top = ranked[0]
        margin = top["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)
        observations.append(
            {
                "correct": top["open_id"] == truth_key.open_id,
                "score": top["score"],
                "margin": margin,
                "intersection": top["intersection"],
            }
        )

    choices = []
    for score_threshold in (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        for margin_threshold in (0.00, 0.03, 0.05, 0.10, 0.15, 0.20):
            for minimum_intersection in (1, 2, 3):
                selected = [
                    row
                    for row in observations
                    if row["score"] >= score_threshold
                    and row["margin"] >= margin_threshold
                    and row["intersection"] >= minimum_intersection
                ]
                if not selected:
                    continue
                precision = sum(row["correct"] for row in selected) / len(selected)
                coverage_rate = len(selected) / len(observations) if observations else 0.0
                if precision >= 0.99:
                    choices.append(
                        (
                            coverage_rate,
                            precision,
                            -score_threshold,
                            -margin_threshold,
                            -minimum_intersection,
                            score_threshold,
                            margin_threshold,
                            minimum_intersection,
                            len(selected),
                        )
                    )
    if choices:
        chosen = max(choices)
        score_threshold, margin_threshold, minimum_intersection = chosen[5:8]
        selected_count = chosen[8]
        precision = chosen[1]
        coverage_rate = chosen[0]
    else:
        score_threshold, margin_threshold, minimum_intersection = 0.80, 0.15, 3
        selected_count = 0
        precision = 0.0
        coverage_rate = 0.0
    return {
        "year": year,
        "evaluation_profile_count": len(observations),
        "selected_profile_count": selected_count,
        "precision": round(precision, 6),
        "coverage": round(coverage_rate, 6),
        "score_threshold": score_threshold,
        "margin_threshold": margin_threshold,
        "minimum_intersection": minimum_intersection,
        "target_precision": 0.99,
        "caveat": "Masked-ID backtest uses 0104 department profiles as proxies for the employment target schema.",
    }


def resolve_targets(
    targets: list[dict[str, str]],
    target_profiles: dict[str, dict[str, set]],
    academy_profiles: dict[tuple[str, str], dict[str, set]],
    profiles: dict[ProfileKey, CandidateProfile],
    bridge: dict[tuple[str, str], dict[str, set[str]]],
    calibration: dict,
) -> list[dict[str, str]]:
    output = []
    for target in targets:
        identity_key = target["_school_identity_key"]
        year = target["_panel_year"]
        kind = target["edss_school_kind"]
        province = normalize_province(target["edss_province"])
        branch = normalize_text(target.get("edss_branch", ""))
        departments = target_profiles[identity_key]["departments"]
        api_key = (target.get("api_school_name", ""), target.get("api_school_kind", ""))
        academy_profile = academy_profiles.get(api_key)
        group_key = "|".join(
            (
                normalize_text(target["edss_school_name"]),
                normalize_text(kind),
                province,
                branch,
            )
        )
        base = {
            "_panel_year": year,
            "_school_identity_key": identity_key,
            "edss_school_name": target["edss_school_name"],
            "edss_school_kind": kind,
            "edss_province": target["edss_province"],
            "edss_branch": target.get("edss_branch", ""),
            "name_context_status": target["match_status"],
            "target_department_count": str(len(departments)),
            "cross_year_group_key": group_key,
            "cross_year_consistent": "not_tested",
            "year_conflict_excluded": "false",
            "reverse_collision_excluded": "false",
            "evidence_sources": "EDSS employment 2023-2024|EDSS 0402-0404|EDSS 0101 bridge|EDSS 0104|AcademyInfo 2025",
            "canonical_open_id_imputed": "false",
        }
        if target["match_status"] != "candidate_unique_name_context":
            base.update(
                {
                    "context_candidate_count": "0",
                    "overlap_candidate_count": "0",
                    "top_candidate_open_id": "",
                    "candidate_signature_year": "",
                    "candidate_open_id": "",
                    "candidate_method": "",
                    "confidence_tier": "unresolved",
                    "resolution_status": "unresolved_name_context",
                    "score": "",
                    "score_margin": "",
                    "department_intersection_count": "",
                    "department_union_count": "",
                    "department_jaccard": "",
                    "target_department_coverage": "",
                    "candidate_department_coverage": "",
                    "academyinfo_department_jaccard": "",
                    "academyinfo_day_night_jaccard": "",
                    "cross_panel_department_jaccard": "",
                }
            )
            output.append(base)
            continue

        ranked = rank_candidates(
            year,
            kind,
            province,
            branch,
            departments,
            academy_profile,
            profiles,
            bridge,
        )
        overlap = [row for row in ranked if row["intersection"] > 0]
        top = overlap[0] if overlap else None
        second_score = overlap[1]["score"] if len(overlap) > 1 else 0.0
        margin = top["score"] - second_score if top else None
        exact = [row for row in ranked if row["exact"]]
        selected = ""
        method = ""
        tier = "unresolved"
        if not departments:
            status = "unresolved_empty_department_signature"
        elif top is None:
            status = "unresolved_no_department_overlap"
        elif len(exact) == 1 and len(departments) >= 3 and exact[0]["signature_year"] == year:
            selected = exact[0]["open_id"]
            top = exact[0]
            method = "unique_exact_kind_year_region_branch_department_set"
            tier = "high"
            status = "candidate_high_exact_context"
        elif len(exact) == 1:
            selected = exact[0]["open_id"]
            top = exact[0]
            method = (
                "unique_exact_small_department_set_with_context"
                if exact[0]["signature_year"] == year
                else "unique_exact_prior_year_signature_with_current_context"
            )
            tier = "strong"
            status = (
                "candidate_strong_exact_small_signature"
                if exact[0]["signature_year"] == year
                else "candidate_strong_exact_prior_year_signature"
            )
        elif (
            top["score"] >= calibration["score_threshold"]
            and (margin or 0.0) >= calibration["margin_threshold"]
            and top["intersection"] >= calibration["minimum_intersection"]
            and (top["target_coverage"] or 0.0) >= 0.60
        ):
            selected = top["open_id"]
            method = "calibrated_multisource_rank_kind_year_region_branch"
            tier = "strong"
            status = "candidate_strong_calibrated_multisource"
        elif len(exact) > 1:
            status = "unresolved_ambiguous_exact_signature"
        else:
            status = "unresolved_below_calibrated_threshold"

        base.update(
            {
                "context_candidate_count": str(len(ranked)),
                "overlap_candidate_count": str(len(overlap)),
                "top_candidate_open_id": top["open_id"] if top else "",
                "candidate_signature_year": top["signature_year"] if top else "",
                "candidate_open_id": selected,
                "candidate_method": method,
                "confidence_tier": tier,
                "resolution_status": status,
                "score": fmt(top["score"] if top else None),
                "score_margin": fmt(margin),
                "department_intersection_count": str(top["intersection"]) if top else "",
                "department_union_count": str(top["union"]) if top else "",
                "department_jaccard": fmt(top["department_jaccard"] if top else None),
                "target_department_coverage": fmt(top["target_coverage"] if top else None),
                "candidate_department_coverage": fmt(top["candidate_coverage"] if top else None),
                "academyinfo_department_jaccard": fmt(top["academy_jaccard"] if top else None),
                "academyinfo_day_night_jaccard": fmt(top["day_night_jaccard"] if top else None),
                "cross_panel_department_jaccard": fmt(top["cross_panel_jaccard"] if top else None),
            }
        )
        output.append(base)

    apply_cross_year_rules(output, profiles)
    apply_global_consistency_rules(output)
    return output


def apply_cross_year_rules(rows: list[dict[str, str]], profiles: dict[ProfileKey, CandidateProfile]) -> None:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["cross_year_group_key"]].append(row)
    for group_rows in grouped.values():
        by_year = {row["_panel_year"]: row for row in group_rows}
        if set(by_year) != {"2023", "2024"}:
            for row in group_rows:
                row["cross_year_consistent"] = "not_tested_single_year"
            continue
        left, right = by_year["2023"], by_year["2024"]
        left_top, right_top = left["top_candidate_open_id"], right["top_candidate_open_id"]
        if left_top and left_top == right_top:
            left["cross_year_consistent"] = right["cross_year_consistent"] = "true"
            for row in (left, right):
                if not row["candidate_open_id"] and float(row["score"] or 0) >= 0.45:
                    has_profile = any(
                        key.school_kind == row["edss_school_kind"]
                        and key.open_id == left_top
                        and key.year <= row["_panel_year"]
                        for key in profiles
                    )
                    if has_profile:
                        row["candidate_open_id"] = left_top
                        row["candidate_method"] = "cross_year_consistent_top_candidate_anchor"
                        row["confidence_tier"] = "strong"
                        row["resolution_status"] = "candidate_strong_cross_year_anchor"
        elif left["candidate_open_id"] and right["candidate_open_id"]:
            left["cross_year_consistent"] = right["cross_year_consistent"] = "false"
            for row in (left, right):
                row["candidate_open_id"] = ""
                row["confidence_tier"] = "unresolved"
                row["resolution_status"] = "excluded_cross_year_candidate_conflict"
                row["year_conflict_excluded"] = "true"
        else:
            left["cross_year_consistent"] = right["cross_year_consistent"] = "inconclusive"


def apply_global_consistency_rules(rows: list[dict[str, str]]) -> None:
    """Exclude reverse collisions: one ID selecting multiple schools in one year."""
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["candidate_open_id"]:
            grouped[(row["_panel_year"], row["candidate_open_id"])].append(row)
    for collision_rows in grouped.values():
        groups = {row["cross_year_group_key"] for row in collision_rows}
        if len(groups) <= 1:
            continue
        for row in collision_rows:
            row["candidate_open_id"] = ""
            row["confidence_tier"] = "unresolved"
            row["resolution_status"] = "excluded_reverse_candidate_collision"
            row["reverse_collision_excluded"] = "true"


def atomic_write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    with partial.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(partial, path)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def build_summary(
    rows: list[dict[str, str]],
    calibration: dict,
    input_paths: dict[str, Path],
    output_path: Path,
) -> dict:
    keys = [row["_school_identity_key"] for row in rows]
    status_counts = Counter(row["resolution_status"] for row in rows)
    tier_counts = Counter(row["confidence_tier"] for row in rows)
    selected = [row for row in rows if row["candidate_open_id"]]
    critical_missing = {
        field: sum(not row[field] for row in rows)
        for field in ("_panel_year", "_school_identity_key", "edss_school_name", "edss_school_kind")
    }
    input_receipt = {}
    duckdb_build = input_paths.get("duckdb_build")
    recorded_database = {}
    if duckdb_build and duckdb_build.exists():
        recorded_database = json.loads(duckdb_build.read_text(encoding="utf-8")).get("database", {})
    for name, path in input_paths.items():
        if name == "duckdb":
            input_receipt[name] = {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": recorded_database.get("sha256", ""),
                "sha256_source": str(duckdb_build) if recorded_database.get("sha256") else "",
            }
        else:
            input_receipt[name] = {"path": str(path), "sha256": sha256_file(path)}
    return {
        "generated_at": utc_now(),
        "status": "review_required",
        "population": "EDSS employment 2023-2024 graduate-school-year identities",
        "row_count": len(rows),
        "unique_identity_key_count": len(set(keys)),
        "duplicate_identity_key_count": len(keys) - len(set(keys)),
        "critical_missing_counts": critical_missing,
        "name_context_matched_count": sum(
            row["name_context_status"] == "candidate_unique_name_context" for row in rows
        ),
        "selected_candidate_count": len(selected),
        "selected_unique_open_id_count": len({row["candidate_open_id"] for row in selected}),
        "confidence_tier_counts": dict(sorted(tier_counts.items())),
        "resolution_status_counts": dict(sorted(status_counts.items())),
        "cross_year_conflict_excluded_row_count": sum(
            row["year_conflict_excluded"] == "true" for row in rows
        ),
        "reverse_collision_excluded_row_count": sum(
            row["reverse_collision_excluded"] == "true" for row in rows
        ),
        "canonical_open_id_imputed_row_count": 0,
        "masked_id_backtest": calibration,
        "inputs": input_receipt,
        "output": {
            "path": str(output_path),
            "row_count": len(rows),
            "sha256": sha256_file(output_path),
        },
        "limitations": [
            "Candidates are statistical inferences, not an official EDSS crosswalk.",
            "AcademyInfo is survey year 2025 while target identities are 2023-2024.",
            "Department names and reporting grain can change across panels and years.",
            "Candidate OpenIDs remain separate evidence labels; canonical OpenIDs are not imputed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument(
        "--duckdb",
        type=Path,
        default=None,
        help="Defaults to SOURCE_ROOT/data/processed/edss/restricted/edss_all.duckdb",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/metadata/edss_graduate_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/metadata/edss_graduate_open_id_inference.json"),
    )
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    paths = {
        "name_candidates": source_root / "data/metadata/edss_academyinfo_graduate_name_candidates.csv",
        "identity_candidates": source_root / "data/metadata/edss_employment_2023_2024_open_id_candidates.csv",
        "employment_aggregate": source_root / "data/processed/edss/derived/employment_2023_2024_school_department.csv.gz",
        "academyinfo_graduate_major": source_root / "data/raw/open_api/academyinfo_school_major/graduate_school_major.csv",
        "school_year_bridge": source_root / "data/metadata/edss_school_year_bridge.csv",
        "duckdb_build": source_root / "data/metadata/edss_duckdb_build.json",
        "duckdb": args.duckdb
        or source_root / "data/processed/edss/restricted/edss_all.duckdb",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing inputs: {missing}")
    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError("duckdb is required; install the pinned project runtime dependency") from error

    targets, target_profiles = load_target_profiles(
        paths["name_candidates"],
        paths["identity_candidates"],
        paths["employment_aggregate"],
    )
    academy_profiles = load_academyinfo_profiles(paths["academyinfo_graduate_major"])
    bridge = load_bridge(paths["school_year_bridge"])
    connection = duckdb.connect(str(paths["duckdb"]), read_only=True)
    try:
        profiles = load_candidate_profiles(connection, ("2022", "2023", "2024"))
    finally:
        connection.close()
    calibration = calibrate_backtest(profiles, bridge)
    rows = resolve_targets(
        targets,
        target_profiles,
        academy_profiles,
        profiles,
        bridge,
        calibration,
    )
    rows.sort(key=lambda row: (row["_panel_year"], row["edss_school_kind"], row["edss_school_name"]))
    if len(rows) != len({row["_school_identity_key"] for row in rows}):
        raise RuntimeError("output identity key is not unique")
    atomic_write_csv(args.output, rows)
    summary = build_summary(rows, calibration, paths, args.output)
    atomic_write_json(args.report, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
