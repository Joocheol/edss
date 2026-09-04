#!/usr/bin/env python3
"""Create safe post-2022 employment outputs and review high orphan panels.

The script never imputes the canonical ``개방ID``. A candidate is recorded only
when a current school's complete department signature matches one historical
OpenID and its same-year 0101 province/branch context is consistent.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


IDENTITY_FIELDS = ("학교명", "본분교명", "시도명", "학교종류명")
SIGNATURE_FIELDS = ("학과명", "단과대학명")
PUBLIC_FIELDS = (
    "_panel_year",
    "학교명",
    "본분교명",
    "시도명",
    "학교종류명",
    "대학대학원구분명",
    "대학계열명",
    "단과대학명",
    "학과명",
    "건강보험가입취업인원수",
    "해외취업인원수",
    "농림어업취업자인원수",
    "개인창작활동종사자인원수",
    "1인사업인원수",
    "무소속근무자인원수",
    "진학자인원수",
    "군입대인원수",
    "취업불가능인원수",
    "외국인유학생인원수",
    "제외인정자인원수",
    "기타인원수",
    "미상인원수",
    "취업비율",
    "진학비율",
)
PROVENANCE_FIELDS = (
    "_source_archive",
    "_source_member",
    "_source_row_number",
    "_source_row_id",
)
DERIVED_FIELDS = PUBLIC_FIELDS + (
    "_school_identity_key",
    "_open_id_candidate",
    "_open_id_candidate_method",
    "_open_id_candidate_status",
) + PROVENANCE_FIELDS
CANDIDATE_FIELDS = (
    "_panel_year",
    "_school_identity_key",
    "학교명",
    "본분교명",
    "시도명",
    "학교종류명",
    "source_row_count",
    "department_count",
    "department_college_pair_count",
    "candidate_open_id",
    "candidate_method",
    "bridge_year_exists",
    "province_context_match",
    "branch_context_match",
    "resolution_status",
)
HIGH_REVIEW_FIELDS = (
    "source",
    "catalog_code",
    "dataset",
    "panel_row_count",
    "orphan_key_count",
    "orphan_row_count",
    "orphan_row_rate",
    "boundary_key_count",
    "boundary_row_count",
    "internal_gap_key_count",
    "internal_gap_row_count",
    "never_in_0101_key_count",
    "never_in_0101_row_count",
    "manually_resolved_key_count",
    "manually_resolved_row_count",
    "review_disposition",
    "recommended_handling",
)
HIGH_MANUAL_DECISION_FIELDS = (
    "source",
    "catalog_code",
    "year",
    "open_id",
    "expected_row_count",
    "decision_status",
    "manual_classification",
    "confirmed_entity_name",
    "evidence_url",
    "evidence_summary",
    "recommended_handling",
)
CLASSIFICATION_MAP = {
    "before_first_0101_year": "boundary",
    "after_last_0101_year": "boundary",
    "internal_0101_gap": "internal_gap",
    "never_in_0101": "never",
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
    "강원도": "강원",
    "강원특별자치도": "강원",
    "충청북도": "충북",
    "충청남도": "충남",
    "전라북도": "전북",
    "전북특별자치도": "전북",
    "전라남도": "전남",
    "경상북도": "경북",
    "경상남도": "경남",
    "제주특별자치도": "제주",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def normalize_province(value: str) -> str:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
    return normalize_text(PROVINCE_ALIASES.get(compact, compact))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity_key(year: str, normalized_values: tuple[str, ...]) -> str:
    payload = "\x1f".join((year,) + normalized_values).encode("utf-8")
    return "employment-school-" + hashlib.sha256(payload).hexdigest()[:20]


def read_bridge(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    lookup = {(row["_panel_year"], row["개방ID"]): row for row in rows}
    if len(lookup) != len(rows):
        raise RuntimeError("school-year bridge key is not unique")
    return lookup


def load_high_panel_manual_decisions(path: Path) -> dict[tuple[str, str, str, str], dict[str, str]]:
    """Load approved high-panel decisions without changing any source row or OpenID."""

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"high-panel manual decision file is empty: {path}")
    missing = set(HIGH_MANUAL_DECISION_FIELDS) - set(rows[0])
    if missing:
        raise RuntimeError(f"high-panel manual decision fields missing: {sorted(missing)}")

    decisions: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = tuple(row[field].strip() for field in ("source", "catalog_code", "year", "open_id"))
        if key in decisions:
            raise RuntimeError(f"duplicate high-panel manual decision: {key}")
        if row["decision_status"].strip() != "approved":
            raise RuntimeError(f"high-panel decision is not approved: {key}")
        try:
            expected_rows = int(row["expected_row_count"])
        except ValueError as error:
            raise RuntimeError(f"invalid expected row count for {key}") from error
        if expected_rows <= 0:
            raise RuntimeError(f"nonpositive expected row count for {key}: {expected_rows}")
        if not row["manual_classification"].strip().startswith("confirmed_"):
            raise RuntimeError(f"unconfirmed high-panel classification for {key}")
        handling = row["recommended_handling"].strip()
        if "retain_same_open_id" not in handling or "no_imputation" not in handling:
            raise RuntimeError(f"unsafe high-panel handling for {key}: {handling!r}")
        if not row["evidence_url"].strip() or not row["evidence_summary"].strip():
            raise RuntimeError(f"high-panel decision lacks evidence for {key}")
        decisions[key] = {field: row[field].strip() for field in HIGH_MANUAL_DECISION_FIELDS}
    return decisions


def split_normalized(value: str, normalizer=normalize_text) -> set[str]:
    return {normalizer(item) for item in value.split("|") if normalizer(item)}


def bridge_context(candidate: str, year: str, identity: tuple[str, ...], bridge: dict) -> tuple[str, str, str]:
    row = bridge.get((year, candidate))
    if row is None:
        return "false", "not_tested", "not_tested"
    _school, branch, province, _kind = identity
    provinces = split_normalized(row["_0101_provinces"], normalize_province)
    branches = split_normalized(row["_0101_branch_names"])
    province_match = "not_applicable" if not province else ("true" if province in provinces else "false")
    branch_match = "not_applicable" if not branch else ("true" if branch in branches else "false")
    return "true", province_match, branch_match


def candidate_status(bridge_exists: str, province_match: str, branch_match: str) -> str:
    if bridge_exists != "true":
        return "candidate_missing_same_year_0101"
    if province_match == "false" or branch_match == "false":
        return "candidate_0101_context_conflict"
    return "candidate_signature_context_confirmed"


def build_employment_outputs(
    panel_path: Path,
    bridge_path: Path,
    minimum_signature_size: int = 3,
) -> tuple[list[dict], list[dict], dict]:
    bridge = read_bridge(bridge_path)
    historical_pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    historical_departments: dict[str, set[str]] = defaultdict(set)
    current_pairs: dict[tuple[str, ...], set[tuple[str, str]]] = defaultdict(set)
    current_departments: dict[tuple[str, ...], set[str]] = defaultdict(set)
    current_rows: list[tuple[dict[str, str], tuple[str, ...]]] = []

    with gzip.open(panel_path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"개방ID", *IDENTITY_FIELDS, *SIGNATURE_FIELDS, *PUBLIC_FIELDS, *PROVENANCE_FIELDS}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError(f"employment panel missing required fields: {missing}")
        for row in reader:
            year = row["_panel_year"].strip()
            open_id = row["개방ID"].strip()
            department = normalize_text(row["학과명"])
            college = normalize_text(row["단과대학명"])
            if open_id:
                if department:
                    historical_pairs[open_id].add((department, college))
                    historical_departments[open_id].add(department)
                continue
            identity = (
                normalize_text(row["학교명"]),
                normalize_text(row["본분교명"]),
                normalize_province(row["시도명"]),
                normalize_text(row["학교종류명"]),
            )
            if year not in {"2023", "2024"}:
                raise RuntimeError(f"unexpected missing OpenID year: {year}")
            if not identity[0]:
                raise RuntimeError(f"missing school name in post-2022 employment row: {row['_source_row_id']}")
            identity_id = (year,) + identity
            if department:
                current_pairs[identity_id].add((department, college))
                current_departments[identity_id].add(department)
            current_rows.append((row, identity_id))

    pair_index: dict[frozenset, set[str]] = defaultdict(set)
    department_index: dict[frozenset, set[str]] = defaultdict(set)
    for open_id, values in historical_pairs.items():
        if len(values) >= minimum_signature_size:
            pair_index[frozenset(values)].add(open_id)
    for open_id, values in historical_departments.items():
        if len(values) >= minimum_signature_size:
            department_index[frozenset(values)].add(open_id)

    raw_identity_values: dict[tuple[str, ...], tuple[str, ...]] = {}
    identity_row_counts = Counter(identity_id for _row, identity_id in current_rows)
    for row, identity_id in current_rows:
        raw_identity_values.setdefault(identity_id, tuple(row[field].strip() for field in IDENTITY_FIELDS))

    resolutions: dict[tuple[str, ...], dict[str, str]] = {}
    candidate_rows = []
    for identity_id in sorted(identity_row_counts):
        year, *normalized_identity = identity_id
        pair_values = current_pairs[identity_id]
        department_values = current_departments[identity_id]
        pair_candidates = pair_index.get(frozenset(pair_values), set()) if len(pair_values) >= minimum_signature_size else set()
        department_candidates = (
            department_index.get(frozenset(department_values), set())
            if len(department_values) >= minimum_signature_size
            else set()
        )
        candidate = ""
        method = ""
        if len(pair_candidates) == 1:
            candidate = next(iter(pair_candidates))
            method = "unique_exact_department_college_signature"
        elif len(department_candidates) == 1:
            candidate = next(iter(department_candidates))
            method = "unique_exact_department_signature"

        bridge_exists = "not_tested"
        province_match = "not_tested"
        branch_match = "not_tested"
        if candidate:
            bridge_exists, province_match, branch_match = bridge_context(
                candidate,
                year,
                tuple(normalized_identity),
                bridge,
            )
            status = candidate_status(bridge_exists, province_match, branch_match)
        elif len(pair_values) < minimum_signature_size:
            status = "unresolved_signature_too_small"
        elif len(pair_candidates) > 1 or len(department_candidates) > 1:
            status = "unresolved_ambiguous_exact_signature"
        else:
            status = "unresolved_no_exact_signature"

        resolutions[identity_id] = {"candidate": candidate, "method": method, "status": status}
        school, branch, province, kind = raw_identity_values[identity_id]
        candidate_rows.append(
            {
                "_panel_year": year,
                "_school_identity_key": identity_key(year, tuple(normalized_identity)),
                "학교명": school,
                "본분교명": branch,
                "시도명": province,
                "학교종류명": kind,
                "source_row_count": identity_row_counts[identity_id],
                "department_count": len(department_values),
                "department_college_pair_count": len(pair_values),
                "candidate_open_id": candidate,
                "candidate_method": method,
                "bridge_year_exists": bridge_exists,
                "province_context_match": province_match,
                "branch_context_match": branch_match,
                "resolution_status": status,
            }
        )

    derived_rows = []
    for row, identity_id in current_rows:
        resolution = resolutions[identity_id]
        year, *normalized_identity = identity_id
        derived = {field: row[field] for field in PUBLIC_FIELDS}
        derived.update(
            {
                "_school_identity_key": identity_key(year, tuple(normalized_identity)),
                "_open_id_candidate": resolution["candidate"],
                "_open_id_candidate_method": resolution["method"],
                "_open_id_candidate_status": resolution["status"],
            }
        )
        derived.update({field: row[field] for field in PROVENANCE_FIELDS})
        derived_rows.append(derived)

    identity_status = Counter(row["resolution_status"] for row in candidate_rows)
    row_status = Counter(row["_open_id_candidate_status"] for row in derived_rows)
    summary = {
        "minimum_signature_size": minimum_signature_size,
        "historical_open_id_count": len(historical_pairs),
        "source_missing_open_id_row_count": len(current_rows),
        "current_school_year_identity_count": len(candidate_rows),
        "identity_status_counts": dict(sorted(identity_status.items())),
        "row_status_counts": dict(sorted(row_status.items())),
        "candidate_open_id_count": len({row["candidate_open_id"] for row in candidate_rows if row["candidate_open_id"]}),
        "canonical_open_id_imputed_row_count": 0,
        "privacy_rule": "Only the 2023-2024 aggregate schema and provenance fields are written; individual-level fields are excluded.",
        "imputation_rule": "The canonical 개방ID remains absent. Candidate IDs are evidence labels and must not be treated as confirmed mappings.",
    }
    return derived_rows, candidate_rows, summary


def review_high_panels(
    audit_summary_path: Path,
    orphan_path: Path,
    manual_decision_path: Path | None = None,
) -> tuple[list[dict], dict]:
    audit = json.loads(audit_summary_path.read_text(encoding="utf-8"))
    audit_panels = audit.get("high_panels")
    if audit_panels is None:
        audit_panels = [
            row for row in audit.get("highest_risk_panels", [])
            if row.get("severity") == "high"
        ]
    high_keys = {
        (row["source"], row["catalog_code"]): row
        for row in audit_panels
    }
    with orphan_path.open(encoding="utf-8-sig", newline="") as handle:
        orphan_rows = [
            row for row in csv.DictReader(handle)
            if (row["source"], row["catalog_code"]) in high_keys
        ]
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in orphan_rows:
        grouped[(row["source"], row["catalog_code"])].append(row)

    decisions = (
        load_high_panel_manual_decisions(manual_decision_path)
        if manual_decision_path is not None
        else {}
    )
    reviews = []
    manual_keys = []
    resolved_keys = []
    observed_manual_keys: set[tuple[str, str, str, str]] = set()
    for key in sorted(high_keys):
        panel = high_keys[key]
        rows = grouped[key]
        class_keys = Counter(row["classification"] for row in rows)
        class_rows = Counter()
        for row in rows:
            class_rows[CLASSIFICATION_MAP[row["classification"]]] += int(row["row_count"])
            if CLASSIFICATION_MAP[row["classification"]] in {"internal_gap", "never"}:
                decision_key = (row["source"], row["catalog_code"], row["year"], row["open_id"])
                observed_manual_keys.add(decision_key)
                decision = decisions.get(decision_key)
                if decision is None:
                    manual_keys.append(row)
                else:
                    if int(decision["expected_row_count"]) != int(row["row_count"]):
                        raise RuntimeError(
                            f"high-panel decision row-count mismatch for {decision_key}: "
                            f"expected {decision['expected_row_count']}, observed {row['row_count']}"
                        )
                    resolved_keys.append({**row, **decision})
        boundary_only = set(class_keys) <= {"before_first_0101_year", "after_last_0101_year"}
        unresolved_for_panel = [
            row for row in manual_keys
            if (row["source"], row["catalog_code"]) == key
        ]
        resolved_for_panel = [
            row for row in resolved_keys
            if (row["source"], row["catalog_code"]) == key
        ]
        if boundary_only:
            disposition = "explained_temporal_boundary"
            handling = (
                "Preserve unmatched rows; no ID correction. Treat the orphan rate as "
                "reference-period coverage."
            )
        elif unresolved_for_panel:
            disposition = "manual_review_required"
            handling = "Preserve unmatched rows; review unresolved internal gaps before any mapping."
        else:
            disposition = "explained_manual_identity_scope_decision"
            handling = resolved_for_panel[0]["recommended_handling"]
        reviews.append(
            {
                "source": panel["source"],
                "catalog_code": panel["catalog_code"],
                "dataset": panel["dataset"],
                "panel_row_count": panel["row_count"],
                "orphan_key_count": len(rows),
                "orphan_row_count": sum(int(row["row_count"]) for row in rows),
                "orphan_row_rate": panel["orphan_row_rate"],
                "boundary_key_count": sum(
                    count for name, count in class_keys.items()
                    if CLASSIFICATION_MAP[name] == "boundary"
                ),
                "boundary_row_count": class_rows["boundary"],
                "internal_gap_key_count": class_keys["internal_0101_gap"],
                "internal_gap_row_count": class_rows["internal_gap"],
                "never_in_0101_key_count": class_keys["never_in_0101"],
                "never_in_0101_row_count": class_rows["never"],
                "manually_resolved_key_count": len(resolved_for_panel),
                "manually_resolved_row_count": sum(
                    int(row["row_count"]) for row in resolved_for_panel
                ),
                "review_disposition": disposition,
                "recommended_handling": handling,
            }
        )
    if len(reviews) != 4:
        raise RuntimeError(f"expected 4 high panels, found {len(reviews)}")
    unexpected_decisions = set(decisions) - observed_manual_keys
    if unexpected_decisions:
        raise RuntimeError(f"manual decisions do not match high-panel gaps: {sorted(unexpected_decisions)}")
    return reviews, {
        "reviewed_panel_count": len(reviews),
        "explained_temporal_boundary_panel_count": sum(
            row["review_disposition"] == "explained_temporal_boundary" for row in reviews
        ),
        "manually_resolved_panel_count": sum(
            row["review_disposition"] == "explained_manual_identity_scope_decision"
            for row in reviews
        ),
        "manually_resolved_key_count": len(resolved_keys),
        "manual_review_required_panel_count": sum(
            row["review_disposition"] == "manual_review_required" for row in reviews
        ),
        "manual_review_keys": manual_keys,
        "manual_decisions": resolved_keys,
        "status": "complete" if not manual_keys else "review_required",
    }


def atomic_write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def atomic_write_gzip_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as gzip_handle:
            with io.TextIOWrapper(gzip_handle, encoding="utf-8", newline="") as text_handle:
                writer = csv.DictWriter(text_handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
    temp.replace(path)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--employment-panel",
        type=Path,
        default=Path("data/processed/edss/restricted/취업통계/0001_학생인적취업정보/panel.csv.gz"),
    )
    parser.add_argument("--bridge", type=Path, default=Path("data/metadata/edss_school_year_bridge.csv"))
    parser.add_argument(
        "--audit-summary", type=Path, default=Path("data/metadata/edss_full_panel_key_audit.json")
    )
    parser.add_argument(
        "--audit-orphans",
        type=Path,
        default=Path("data/metadata/edss_full_panel_orphan_school_year_keys.csv"),
    )
    parser.add_argument(
        "--derived-output",
        type=Path,
        default=Path("data/processed/edss/derived/employment_2023_2024_school_department.csv.gz"),
    )
    parser.add_argument(
        "--candidate-output",
        type=Path,
        default=Path("data/metadata/edss_employment_2023_2024_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--high-review-output",
        type=Path,
        default=Path("data/metadata/edss_high_orphan_panel_review.csv"),
    )
    parser.add_argument(
        "--high-manual-decisions",
        type=Path,
        default=Path("data/metadata/edss_high_orphan_manual_decisions.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("data/metadata/edss_remaining_identity_gap_resolution.json"),
    )
    parser.add_argument("--minimum-signature-size", type=int, default=3)
    args = parser.parse_args()

    derived_rows, candidate_rows, employment_summary = build_employment_outputs(
        args.employment_panel,
        args.bridge,
        minimum_signature_size=args.minimum_signature_size,
    )
    high_reviews, high_summary = review_high_panels(
        args.audit_summary,
        args.audit_orphans,
        args.high_manual_decisions,
    )
    atomic_write_gzip_csv(args.derived_output, derived_rows, DERIVED_FIELDS)
    atomic_write_csv(args.candidate_output, candidate_rows, CANDIDATE_FIELDS)
    atomic_write_csv(args.high_review_output, high_reviews, HIGH_REVIEW_FIELDS)
    summary = {
        "generated_at": utc_now(),
        "status": "review_required",
        "inputs": {
            "high_panel_manual_decisions": {
                "path": str(args.high_manual_decisions),
                "row_count": len(load_high_panel_manual_decisions(args.high_manual_decisions)),
                "sha256": sha256_file(args.high_manual_decisions),
            }
        },
        "employment": employment_summary,
        "high_orphan_panels": high_summary,
        "outputs": {
            "derived_employment": {
                "path": str(args.derived_output),
                "row_count": len(derived_rows),
                "sha256": sha256_file(args.derived_output),
            },
            "employment_candidates": {
                "path": str(args.candidate_output),
                "row_count": len(candidate_rows),
                "sha256": sha256_file(args.candidate_output),
            },
            "high_panel_review": {
                "path": str(args.high_review_output),
                "row_count": len(high_reviews),
                "sha256": sha256_file(args.high_review_output),
            },
        },
    }
    atomic_write_json(args.summary_output, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "derived_employment_rows": len(derived_rows),
                "candidate_identities": sum(
                    row["candidate_open_id"] != "" for row in candidate_rows
                ),
                "context_confirmed_identities": sum(
                    row["resolution_status"] == "candidate_signature_context_confirmed"
                    for row in candidate_rows
                ),
                "high_panels_reviewed": len(high_reviews),
                "high_panels_manual_review": high_summary["manual_review_required_panel_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
