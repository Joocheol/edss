#!/usr/bin/env python3
"""Refine the 2022–2023 provisional crosswalk with college-department pairs.

Candidate eligibility still follows the user's adjacent-year hypothesis:
same province and comparable scope, with distinct department counts differing
by at most two.  Ranking and acceptance then use normalized
``(단과대학명, 학과명)`` pairs before department-only metrics.

All identity-bearing outputs are review-only and stay under the restricted
derived directory.  No canonical panel is modified.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import build_edss_employment_2022_2023_provisional_crosswalk as previous
import match_edss_employment_2022_2023_school_counts as base


REVIEW_FIELDS = (
    "scope",
    "province",
    "school_name_2023",
    "school_kinds_2023",
    "branches_2023",
    "top_open_id_2022",
    "department_count_2022",
    "department_count_2023",
    "department_count_difference",
    "department_overlap_count",
    "department_jaccard",
    "department_smaller_set_coverage",
    "college_department_pair_count_2022",
    "college_department_pair_count_2023",
    "college_department_pair_count_difference",
    "college_department_pair_overlap_count",
    "college_department_pair_jaccard",
    "college_department_pair_smaller_set_coverage",
    "forward_candidate_count",
    "forward_top_tie_count",
    "reverse_candidate_count",
    "reverse_top_tie_count",
    "reciprocal_best",
    "pair_match_status",
    "previously_accepted",
    "previous_open_id_2022",
    "comparison_outcome",
    "open_id_2023_candidate",
    "open_id_2023_evidence_source",
    "open_id_2023_resolution_status",
)

CROSSWALK_FIELDS = (
    "scope",
    "province",
    "school_name_2023",
    "open_id_2022",
    "open_id_2023_candidate",
    "open_id_2023_evidence_source",
    "open_id_2023_resolution_status",
    "department_count_2022",
    "department_count_2023",
    "department_count_difference",
    "department_overlap_count",
    "department_jaccard",
    "college_department_pair_count_2022",
    "college_department_pair_count_2023",
    "college_department_pair_count_difference",
    "college_department_pair_overlap_count",
    "college_department_pair_jaccard",
    "college_department_pair_smaller_set_coverage",
    "pair_match_status",
    "comparison_outcome",
    "confidence_tier",
    "canonical_mapping_written",
)


def load_2022_signatures(path: Path) -> tuple[dict[str, dict[str, set]], Counter]:
    profiles: dict[str, dict[str, set]] = {}
    quality = Counter()
    for row in base.nested_csv_rows(path, "cp949"):
        open_id = (row.get("개방ID") or "").strip()
        college = base.normalize_text(row.get("단과대학명") or "")
        department = base.normalize_text(row.get("학과명") or "")
        quality["row_count"] += 1
        quality["blank_open_id_count"] += not bool(open_id)
        quality["blank_college_count"] += not bool(college)
        quality["blank_department_count"] += not bool(department)
        if not open_id or not college or not department:
            raise RuntimeError("2022 source contains a blank OpenID, college, or department")
        profile = profiles.setdefault(open_id, {"departments": set(), "pairs": set()})
        profile["departments"].add(department)
        profile["pairs"].add((college, department))
    quality["profile_count"] = len(profiles)
    return profiles, quality


def load_2023_signatures(path: Path) -> tuple[dict[tuple[str, str, str], dict], Counter]:
    profiles: dict[tuple[str, str, str], dict] = {}
    quality = Counter()
    for row in base.nested_csv_rows(path, "cp949"):
        province = (row.get("시도명") or "").strip()
        school_name = (row.get("학교명") or "").strip()
        level = (row.get("대학대학원구분명") or "").strip()
        college = base.normalize_text(row.get("단과대학명") or "")
        department = base.normalize_text(row.get("학과명") or "")
        quality["row_count"] += 1
        quality["blank_province_count"] += not bool(province)
        quality["blank_school_name_count"] += not bool(school_name)
        quality["blank_college_count"] += not bool(college)
        quality["blank_department_count"] += not bool(department)
        if not province or not school_name or level not in {"대학", "대학원"} or not college or not department:
            raise RuntimeError("2023 source contains a blank or invalid identity, college, or department")
        key = (province, school_name, level)
        profile = profiles.setdefault(
            key,
            {"departments": set(), "pairs": set(), "branches": set(), "school_kinds": set()},
        )
        profile["departments"].add(department)
        profile["pairs"].add((college, department))
        profile["branches"].add((row.get("본분교명") or "").strip())
        profile["school_kinds"].add((row.get("학교종류명") or "").strip())
    quality["profile_count"] = len(profiles)
    return profiles, quality


def select_comparable(profiles: dict) -> dict:
    return {
        key: profile
        for key, profile in profiles.items()
        if key[2] == "대학" or "일반대학원" in profile["school_kinds"]
    }


def combined_metrics(left: dict[str, set], right: dict[str, set]) -> dict:
    department = base.candidate_metrics(left["departments"], right["departments"])
    pair = base.candidate_metrics(left["pairs"], right["pairs"])
    return {
        "department_count_difference": department["department_count_difference"],
        "department_overlap_count": department["department_overlap_count"],
        "department_union_count": department["department_union_count"],
        "department_jaccard": department["department_jaccard"],
        "department_smaller_set_coverage": department["smaller_set_coverage"],
        "pair_count_difference": pair["department_count_difference"],
        "pair_overlap_count": pair["department_overlap_count"],
        "pair_union_count": pair["department_union_count"],
        "pair_jaccard": pair["department_jaccard"],
        "pair_smaller_set_coverage": pair["smaller_set_coverage"],
    }


def ranking_key(row: dict) -> tuple:
    return (
        -row["pair_overlap_count"],
        -row["pair_jaccard"],
        -row["pair_smaller_set_coverage"],
        -row["department_overlap_count"],
        -row["department_jaccard"],
        -row["department_smaller_set_coverage"],
        row["department_count_difference"],
        row["pair_count_difference"],
    )


def score_key(row: dict) -> tuple:
    return tuple(round(-value, 12) if isinstance(value, float) else -value for value in ranking_key(row))


def rank_open_ids(
    target: dict[str, set], open_ids, profiles_2022: dict[str, dict[str, set]], tolerance: int
) -> list[dict]:
    pairs = []
    for open_id in open_ids:
        metrics = combined_metrics(profiles_2022[open_id], target)
        if metrics["department_count_difference"] <= tolerance:
            pairs.append({"candidate_open_id": open_id, **metrics})
    pairs.sort(key=lambda row: (*ranking_key(row), row["candidate_open_id"]))
    return pairs


def rank_targets(
    source: dict[str, set], target_keys, profiles_2023: dict, tolerance: int
) -> list[dict]:
    pairs = []
    for target_key in target_keys:
        metrics = combined_metrics(source, profiles_2023[target_key])
        if metrics["department_count_difference"] <= tolerance:
            pairs.append({"target_key": target_key, **metrics})
    pairs.sort(key=lambda row: (*ranking_key(row), row["target_key"]))
    return pairs


def top_tie_count(pairs: list[dict]) -> int:
    if not pairs:
        return 0
    best = score_key(pairs[0])
    return sum(score_key(pair) == best for pair in pairs)


def pair_evidence_class(pair: dict, exact_pairs: bool) -> str:
    if exact_pairs and pair["pair_overlap_count"] >= 3:
        return "exact"
    if exact_pairs:
        return "small_exact"
    if (
        pair["pair_overlap_count"] >= 3
        and pair["pair_jaccard"] >= 0.80
        and pair["pair_smaller_set_coverage"] >= 0.90
    ):
        return "high_overlap"
    return "weak"


def load_previous_matches(path: Path) -> dict[tuple[str, str, str], str]:
    matches = {}
    for row in previous.load_csv(path):
        key = (row["scope"], base.normalize_text(row["province"]), base.normalize_text(row["school_name_2023"]))
        matches[key] = row["open_id_2022"]
    return matches


def compare_outcome(
    previous_id: str, current_id: str, current_accepted: bool
) -> str:
    if previous_id and current_accepted and previous_id == current_id:
        return "previous_match_confirmed_by_college_department"
    if previous_id and current_accepted and previous_id != current_id:
        return "previous_match_changed_by_college_department"
    if previous_id:
        return "previous_match_downgraded_by_college_department"
    if current_accepted:
        return "new_match_from_college_department_disambiguation"
    return "not_accepted"


def build_matches(
    profiles_2022: dict[str, dict[str, set]],
    profiles_2023: dict,
    bridge: dict[str, dict[str, str]],
    same_year_evidence: dict,
    previous_matches: dict[tuple[str, str, str], str],
    tolerance: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict]:
    department_profiles_2022 = {
        open_id: profile["departments"] for open_id, profile in profiles_2022.items()
    }
    context_index, _contexts, bridge_missing = previous.build_context_index(
        department_profiles_2022, bridge
    )
    target_context: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for key in profiles_2023:
        target_context[(key[0], key[2])].append(key)

    reverse_rankings = {}
    for (province, level), open_ids in context_index.items():
        targets = target_context.get((province, level), [])
        for open_id in open_ids:
            reverse_rankings[(province, level, open_id)] = rank_targets(
                profiles_2022[open_id], targets, profiles_2023, tolerance
            )

    review_rows = []
    accepted_rows = []
    status_counts = Counter()
    scope_status_counts: dict[str, Counter] = defaultdict(Counter)
    comparison_counts = Counter()
    for key, target in sorted(profiles_2023.items()):
        province, school_name, level = key
        scope = "대학" if level == "대학" else "일반대학원"
        forward = rank_open_ids(
            target, context_index.get((province, level), set()), profiles_2022, tolerance
        )
        forward_ties = top_tie_count(forward)
        best = forward[0] if forward else None
        reverse = []
        reverse_ties = 0
        reciprocal = False
        evidence_class = "none"
        if best:
            open_id = best["candidate_open_id"]
            reverse = reverse_rankings.get((province, level, open_id), [])
            reverse_ties = top_tie_count(reverse)
            reciprocal = bool(reverse and reverse_ties == 1 and reverse[0]["target_key"] == key)
            evidence_class = pair_evidence_class(
                best, profiles_2022[open_id]["pairs"] == target["pairs"]
            )

        if not forward:
            status = "no_department_count_tolerance_candidate"
        elif forward_ties > 1:
            status = "ambiguous_forward_pair_score"
        elif evidence_class == "small_exact":
            status = "review_small_exact_college_department_signature"
        elif evidence_class == "weak":
            status = "review_weak_college_department_overlap"
        elif reverse_ties > 1:
            status = "review_reverse_pair_score_tie"
        elif not reciprocal:
            status = "review_not_reciprocal_by_college_department"
        elif evidence_class == "exact":
            status = "accepted_reciprocal_exact_college_department_set"
        else:
            status = "accepted_reciprocal_high_college_department_overlap"

        current_accepted = status.startswith("accepted_")
        previous_key = (scope, base.normalize_text(province), base.normalize_text(school_name))
        previous_id = previous_matches.get(previous_key, "")
        current_id = best["candidate_open_id"] if best else ""
        outcome = compare_outcome(previous_id, current_id, current_accepted)
        open_id_2023, evidence_source, evidence_status = previous.same_year_fields(
            same_year_evidence, province, school_name, scope
        )
        row = {
            "scope": scope,
            "province": province,
            "school_name_2023": school_name,
            "school_kinds_2023": "|".join(sorted(target["school_kinds"])),
            "branches_2023": "|".join(sorted(target["branches"])),
            "top_open_id_2022": current_id,
            "department_count_2022": str(len(profiles_2022[current_id]["departments"])) if best else "",
            "department_count_2023": str(len(target["departments"])),
            "department_count_difference": str(best["department_count_difference"]) if best else "",
            "department_overlap_count": str(best["department_overlap_count"]) if best else "",
            "department_jaccard": f'{best["department_jaccard"]:.6f}' if best else "",
            "department_smaller_set_coverage": f'{best["department_smaller_set_coverage"]:.6f}' if best else "",
            "college_department_pair_count_2022": str(len(profiles_2022[current_id]["pairs"])) if best else "",
            "college_department_pair_count_2023": str(len(target["pairs"])),
            "college_department_pair_count_difference": str(best["pair_count_difference"]) if best else "",
            "college_department_pair_overlap_count": str(best["pair_overlap_count"]) if best else "",
            "college_department_pair_jaccard": f'{best["pair_jaccard"]:.6f}' if best else "",
            "college_department_pair_smaller_set_coverage": f'{best["pair_smaller_set_coverage"]:.6f}' if best else "",
            "forward_candidate_count": str(len(forward)),
            "forward_top_tie_count": str(forward_ties),
            "reverse_candidate_count": str(len(reverse)),
            "reverse_top_tie_count": str(reverse_ties),
            "reciprocal_best": str(reciprocal).lower(),
            "pair_match_status": status,
            "previously_accepted": str(bool(previous_id)).lower(),
            "previous_open_id_2022": previous_id,
            "comparison_outcome": outcome,
            "open_id_2023_candidate": open_id_2023,
            "open_id_2023_evidence_source": evidence_source,
            "open_id_2023_resolution_status": evidence_status,
        }
        review_rows.append(row)
        status_counts[status] += 1
        scope_status_counts[scope][status] += 1
        comparison_counts[outcome] += 1
        if current_accepted:
            accepted_rows.append(
                {
                    "scope": scope,
                    "province": province,
                    "school_name_2023": school_name,
                    "open_id_2022": current_id,
                    "open_id_2023_candidate": open_id_2023,
                    "open_id_2023_evidence_source": evidence_source,
                    "open_id_2023_resolution_status": evidence_status,
                    "department_count_2022": row["department_count_2022"],
                    "department_count_2023": row["department_count_2023"],
                    "department_count_difference": row["department_count_difference"],
                    "department_overlap_count": row["department_overlap_count"],
                    "department_jaccard": row["department_jaccard"],
                    "college_department_pair_count_2022": row["college_department_pair_count_2022"],
                    "college_department_pair_count_2023": row["college_department_pair_count_2023"],
                    "college_department_pair_count_difference": row["college_department_pair_count_difference"],
                    "college_department_pair_overlap_count": row["college_department_pair_overlap_count"],
                    "college_department_pair_jaccard": row["college_department_pair_jaccard"],
                    "college_department_pair_smaller_set_coverage": row["college_department_pair_smaller_set_coverage"],
                    "pair_match_status": status,
                    "comparison_outcome": outcome,
                    "confidence_tier": "high" if open_id_2023 else "strong",
                    "canonical_mapping_written": "false",
                }
            )

    summary = {
        "bridge_missing_open_id_count": len(bridge_missing),
        "review_identity_count": len(review_rows),
        "accepted_pair_validated_count": len(accepted_rows),
        "accepted_with_2023_open_id_candidate_count": sum(
            bool(row["open_id_2023_candidate"]) for row in accepted_rows
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "scope_status_counts": {
            scope: dict(sorted(counts.items())) for scope, counts in sorted(scope_status_counts.items())
        },
        "comparison_to_department_only_counts": dict(sorted(comparison_counts.items())),
    }
    return review_rows, accepted_rows, summary


def write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/edss"))
    parser.add_argument("--bridge", type=Path, default=Path("data/metadata/edss_school_year_bridge.csv"))
    parser.add_argument("--tolerance", type=int, default=2)
    parser.add_argument(
        "--previous-crosswalk",
        type=Path,
        default=Path("data/processed/edss/restricted/derived/employment_2022_2023_provisional_crosswalk.csv"),
    )
    parser.add_argument(
        "--enrollment-candidates",
        type=Path,
        default=Path("data/metadata/edss_employment_enrollment_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--graduate-candidates",
        type=Path,
        default=Path("data/metadata/edss_graduate_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=Path("data/processed/edss/restricted/derived/employment_2022_2023_college_department_review.csv"),
    )
    parser.add_argument(
        "--crosswalk-output",
        type=Path,
        default=Path("data/processed/edss/restricted/derived/employment_2022_2023_college_department_crosswalk.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("data/metadata/edss_employment_2022_2023_college_department_crosswalk.json"),
    )
    args = parser.parse_args()

    source_2022 = args.raw_root / "취업통계/0001_학생인적취업정보_13299/0001_학생인적취업정보_2022.zip"
    source_2023 = args.raw_root / "취업통계/0001_학생인적취업정보_13300/0001_학생인적취업정보_2023.zip"
    profiles_2022, quality_2022 = load_2022_signatures(source_2022)
    profiles_2023_all, quality_2023 = load_2023_signatures(source_2023)
    profiles_2023 = select_comparable(profiles_2023_all)
    bridge = base.load_bridge(args.bridge)
    same_year = previous.load_same_year_open_id_evidence(
        args.enrollment_candidates, args.graduate_candidates
    )
    previous_matches = load_previous_matches(args.previous_crosswalk)
    review_rows, accepted_rows, counts = build_matches(
        profiles_2022, profiles_2023, bridge, same_year, previous_matches, args.tolerance
    )
    write_csv(args.review_output, REVIEW_FIELDS, review_rows)
    write_csv(args.crosswalk_output, CROSSWALK_FIELDS, accepted_rows)

    summary = {
        "generated_at": base.utc_now(),
        "status": "provisional_review_required",
        "method": {
            "candidate_context": ["province", "university_or_general_graduate_scope"],
            "candidate_filter": "absolute distinct department-count difference <= 2",
            "ranking_priority": "college-department pair overlap before department-only overlap",
            "accepted_rule": "unique reciprocal best plus exact pair set of at least 3 or high pair overlap",
            "canonical_mapping_written": False,
        },
        "data_quality": {
            "source_2022": dict(sorted(quality_2022.items())),
            "source_2023": dict(sorted(quality_2023.items())),
        },
        "counts": counts,
        "inputs": {
            "source_2022": {"path": str(source_2022), "sha256": base.sha256_file(source_2022)},
            "source_2023": {"path": str(source_2023), "sha256": base.sha256_file(source_2023)},
            "bridge": {"path": str(args.bridge), "sha256": base.sha256_file(args.bridge)},
            "previous_crosswalk": {
                "path": str(args.previous_crosswalk), "sha256": base.sha256_file(args.previous_crosswalk)
            },
        },
        "outputs": {},
        "caveats": [
            "College names can change even when the institution identity is stable, so downgraded department-only matches remain review candidates.",
            "Accepted rows are provisional research evidence, not a canonical identity crosswalk.",
            "Professional and special graduate schools remain outside the comparable one-to-one population.",
        ],
    }
    for label, path, rows in (
        ("review", args.review_output, review_rows),
        ("crosswalk", args.crosswalk_output, accepted_rows),
    ):
        summary["outputs"][label] = {
            "path": str(path), "row_count": len(rows), "sha256": base.sha256_file(path)
        }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
