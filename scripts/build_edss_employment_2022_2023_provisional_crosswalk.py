#!/usr/bin/env python3
"""Build a reviewable 2022 OpenID to 2023 school-name crosswalk.

The comparable 2023 populations are undergraduate institutions and general
graduate schools.  Professional and special graduate schools are profiled as
parent-school groups, but are not forced into the 2022 one-to-one crosswalk.

Accepted rows must be a unique reciprocal best match within province and
university/graduate level.  They remain provisional evidence and are never
written into a canonical EDSS panel.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import match_edss_employment_2022_2023_school_counts as base


REVIEW_FIELDS = (
    "scope",
    "province",
    "school_name_2023",
    "school_kinds_2023",
    "branches_2023",
    "department_count_2023",
    "top_open_id_2022",
    "department_count_2022",
    "department_count_difference",
    "department_overlap_count",
    "department_union_count",
    "department_jaccard",
    "smaller_set_coverage",
    "forward_candidate_count",
    "forward_top_tie_count",
    "reverse_candidate_count",
    "reverse_top_tie_count",
    "reciprocal_best",
    "match_status",
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
    "smaller_set_coverage",
    "match_status",
    "confidence_tier",
    "canonical_mapping_written",
)

PARENT_FIELDS = (
    "province",
    "parent_school_name",
    "parent_resolution_method",
    "graduate_school_identity_count",
    "department_count",
    "graduate_school_kinds",
    "component_school_names",
)

REGION_FIELDS = (
    "province",
    "scope",
    "schools_2022",
    "schools_2023",
    "difference_2023_minus_2022",
)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_context_index(
    profiles_2022: dict[str, set[str]], bridge: dict[str, dict[str, str]]
) -> tuple[dict[tuple[str, str], set[str]], dict[str, dict], list[str]]:
    context_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    contexts: dict[str, dict] = {}
    missing = []
    for open_id in profiles_2022:
        row = bridge.get(open_id)
        if row is None:
            missing.append(open_id)
            continue
        provinces = base.split_values(row.get("_0101_provinces", ""))
        school_types = base.split_values(row.get("_0101_school_types", ""))
        levels = {base.normalize_level(value) for value in school_types}
        if not provinces or not levels:
            missing.append(open_id)
            continue
        contexts[open_id] = {"provinces": provinces, "school_types": school_types, "levels": levels}
        for province in provinces:
            for level in levels:
                context_index[(province, level)].add(open_id)
    return context_index, contexts, missing


def select_comparable_2023_profiles(profiles_2023: dict) -> dict:
    selected = {}
    for key, profile in profiles_2023.items():
        _province, _school_name, level = key
        if level == "대학":
            selected[key] = profile
        elif "일반대학원" in profile["school_kinds"]:
            selected[key] = profile
    return selected


def scope_name(key: tuple[str, str, str], profile: dict) -> str:
    return "대학" if key[2] == "대학" else "일반대학원"


def rank_target_pairs(
    departments_2022: set[str], target_keys: list[tuple[str, str, str]], profiles_2023: dict, tolerance: int
) -> list[dict]:
    pairs = []
    for key in target_keys:
        metrics = base.candidate_metrics(departments_2022, profiles_2023[key]["departments"])
        if metrics["department_count_difference"] <= tolerance:
            pairs.append({"target_key": key, **metrics})
    pairs.sort(
        key=lambda row: (
            -row["department_overlap_count"],
            -row["department_jaccard"],
            -row["smaller_set_coverage"],
            row["department_count_difference"],
            row["target_key"],
        )
    )
    return pairs


def unique_top_count(pairs: list[dict]) -> int:
    if not pairs:
        return 0
    best = base.top_score_key(pairs[0])
    return sum(base.top_score_key(pair) == best for pair in pairs)


def evidence_class(pair: dict, exact_set: bool) -> str:
    if exact_set and pair["department_overlap_count"] >= 3:
        return "exact"
    if exact_set:
        return "small_exact"
    if (
        pair["department_overlap_count"] >= 3
        and pair["department_jaccard"] >= 0.80
        and pair["smaller_set_coverage"] >= 0.90
    ):
        return "high_overlap"
    return "weak"


def normalize_candidate_key(province: str, school_name: str, scope: str) -> tuple[str, str, str]:
    return (base.normalize_text(province), base.normalize_text(school_name), scope)


def load_same_year_open_id_evidence(
    enrollment_path: Path, graduate_path: Path
) -> dict[tuple[str, str, str], dict[str, set[str]]]:
    evidence: dict[tuple[str, str, str], dict[str, set[str]]] = defaultdict(
        lambda: {"ids": set(), "sources": set(), "statuses": set()}
    )
    for row in load_csv(enrollment_path):
        if row.get("_panel_year") != "2023" or not row.get("candidate_open_id"):
            continue
        if not row.get("resolution_status", "").startswith("candidate_"):
            continue
        key = normalize_candidate_key(row.get("시도명", ""), row.get("학교명", ""), "대학")
        evidence[key]["ids"].add(row["candidate_open_id"])
        evidence[key]["sources"].add(enrollment_path.name)
        evidence[key]["statuses"].add(row["resolution_status"])

    for row in load_csv(graduate_path):
        if row.get("_panel_year") != "2023" or row.get("edss_school_kind") != "일반대학원":
            continue
        if not row.get("candidate_open_id") or not row.get("resolution_status", "").startswith("candidate_"):
            continue
        if row.get("year_conflict_excluded") == "true" or row.get("reverse_collision_excluded") == "true":
            continue
        key = normalize_candidate_key(
            row.get("edss_province", ""), row.get("edss_school_name", ""), "일반대학원"
        )
        evidence[key]["ids"].add(row["candidate_open_id"])
        evidence[key]["sources"].add(graduate_path.name)
        evidence[key]["statuses"].add(row["resolution_status"])
    return evidence


def same_year_fields(evidence: dict, province: str, school_name: str, scope: str) -> tuple[str, str, str]:
    item = evidence.get(normalize_candidate_key(province, school_name, scope))
    if not item:
        return "", "", "not_available"
    if len(item["ids"]) != 1:
        return "", "|".join(sorted(item["sources"])), "ambiguous_external_candidates"
    return (
        next(iter(item["ids"])),
        "|".join(sorted(item["sources"])),
        "|".join(sorted(item["statuses"])),
    )


def build_reciprocal_matches(
    profiles_2022: dict[str, set[str]],
    profiles_2023: dict,
    bridge: dict[str, dict[str, str]],
    same_year_evidence: dict,
    tolerance: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], dict]:
    context_index, _contexts, bridge_missing = build_context_index(profiles_2022, bridge)
    target_context: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for key in profiles_2023:
        target_context[(key[0], key[2])].append(key)

    reverse_rankings: dict[tuple[str, str, str], list[dict]] = {}
    for (province, level), open_ids in context_index.items():
        target_keys = sorted(target_context.get((province, level), []))
        for open_id in open_ids:
            reverse_rankings[(province, level, open_id)] = rank_target_pairs(
                profiles_2022[open_id], target_keys, profiles_2023, tolerance
            )

    review_rows = []
    accepted_rows = []
    status_counts = Counter()
    scope_status_counts: dict[str, Counter] = defaultdict(Counter)
    for key, profile in sorted(profiles_2023.items()):
        province, school_name, level = key
        scope = scope_name(key, profile)
        forward = base.rank_candidate_pairs(
            profile["departments"], context_index.get((province, level), set()), profiles_2022, tolerance
        )
        forward_ties = unique_top_count(forward)
        best = forward[0] if forward else None
        reverse = []
        reverse_ties = 0
        reciprocal = False
        evidence = "none"
        if best:
            open_id = best["candidate_open_id"]
            reverse = reverse_rankings.get((province, level, open_id), [])
            reverse_ties = unique_top_count(reverse)
            reciprocal = bool(reverse and reverse_ties == 1 and reverse[0]["target_key"] == key)
            evidence = evidence_class(best, profiles_2022[open_id] == profile["departments"])

        if not forward:
            status = "no_count_tolerance_candidate"
        elif forward_ties > 1:
            status = "ambiguous_forward_top_score"
        elif evidence == "small_exact":
            status = "review_small_exact_signature"
        elif evidence == "weak":
            status = "review_weak_best"
        elif reverse_ties > 1:
            status = "review_reverse_top_score_tie"
        elif not reciprocal:
            status = "review_not_reciprocal"
        elif evidence == "exact":
            status = "accepted_reciprocal_exact"
        else:
            status = "accepted_reciprocal_high_overlap"

        open_id_2023, evidence_source, evidence_status = same_year_fields(
            same_year_evidence, province, school_name, scope
        )
        row = {
            "scope": scope,
            "province": province,
            "school_name_2023": school_name,
            "school_kinds_2023": "|".join(sorted(profile["school_kinds"])),
            "branches_2023": "|".join(sorted(profile["branches"])),
            "department_count_2023": str(len(profile["departments"])),
            "top_open_id_2022": best["candidate_open_id"] if best else "",
            "department_count_2022": str(len(profiles_2022[best["candidate_open_id"]])) if best else "",
            "department_count_difference": str(best["department_count_difference"]) if best else "",
            "department_overlap_count": str(best["department_overlap_count"]) if best else "",
            "department_union_count": str(best["department_union_count"]) if best else "",
            "department_jaccard": f'{best["department_jaccard"]:.6f}' if best else "",
            "smaller_set_coverage": f'{best["smaller_set_coverage"]:.6f}' if best else "",
            "forward_candidate_count": str(len(forward)),
            "forward_top_tie_count": str(forward_ties),
            "reverse_candidate_count": str(len(reverse)),
            "reverse_top_tie_count": str(reverse_ties),
            "reciprocal_best": str(reciprocal).lower(),
            "match_status": status,
            "open_id_2023_candidate": open_id_2023,
            "open_id_2023_evidence_source": evidence_source,
            "open_id_2023_resolution_status": evidence_status,
        }
        review_rows.append(row)
        status_counts[status] += 1
        scope_status_counts[scope][status] += 1
        if status.startswith("accepted_"):
            accepted_rows.append(
                {
                    "scope": scope,
                    "province": province,
                    "school_name_2023": school_name,
                    "open_id_2022": row["top_open_id_2022"],
                    "open_id_2023_candidate": open_id_2023,
                    "open_id_2023_evidence_source": evidence_source,
                    "open_id_2023_resolution_status": evidence_status,
                    "department_count_2022": row["department_count_2022"],
                    "department_count_2023": row["department_count_2023"],
                    "department_count_difference": row["department_count_difference"],
                    "department_overlap_count": row["department_overlap_count"],
                    "department_jaccard": row["department_jaccard"],
                    "smaller_set_coverage": row["smaller_set_coverage"],
                    "match_status": status,
                    "confidence_tier": "high" if open_id_2023 else "strong",
                    "canonical_mapping_written": "false",
                }
            )

    counts_2022 = Counter()
    for (province, level), open_ids in context_index.items():
        counts_2022[(province, "대학" if level == "대학" else "일반대학원")] = len(open_ids)
    counts_2023 = Counter((key[0], scope_name(key, profile)) for key, profile in profiles_2023.items())
    region_rows = []
    for context in sorted(set(counts_2022) | set(counts_2023)):
        province, scope = context
        region_rows.append(
            {
                "province": province,
                "scope": scope,
                "schools_2022": str(counts_2022[context]),
                "schools_2023": str(counts_2023[context]),
                "difference_2023_minus_2022": str(counts_2023[context] - counts_2022[context]),
            }
        )

    summary = {
        "bridge_missing_open_id_count": len(bridge_missing),
        "review_identity_count": len(review_rows),
        "accepted_provisional_count": len(accepted_rows),
        "accepted_with_2023_open_id_candidate_count": sum(bool(row["open_id_2023_candidate"]) for row in accepted_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "scope_status_counts": {
            scope: dict(sorted(counts.items())) for scope, counts in sorted(scope_status_counts.items())
        },
    }
    return review_rows, accepted_rows, region_rows, summary


def undergrad_aliases(profiles_2023: dict) -> list[tuple[str, str]]:
    aliases = set()
    for (_province, school_name, level) in profiles_2023:
        if level != "대학":
            continue
        aliases.add((base.normalize_text(school_name), school_name))
        without_campus = re.sub(r"\s+\S*캠퍼스$", "", school_name).strip()
        if without_campus != school_name:
            aliases.add((base.normalize_text(without_campus), without_campus))
    return sorted(aliases, key=lambda item: (-len(item[0]), item[0], item[1]))


def resolve_parent_school(school_name: str, aliases: list[tuple[str, str]]) -> tuple[str, str]:
    normalized = base.normalize_text(school_name)
    if normalized.endswith("대학원대학교"):
        return school_name, "standalone_graduate_university"
    hits = [(normalized_alias, display) for normalized_alias, display in aliases if normalized.startswith(normalized_alias)]
    if not hits:
        return "", "unresolved_parent"
    longest = len(hits[0][0])
    displays = sorted({display for alias, display in hits if len(alias) == longest})
    if len(displays) != 1:
        return "", "ambiguous_longest_undergraduate_prefix"
    return displays[0], "longest_undergraduate_name_prefix"


def build_parent_groups(profiles_2023_all: dict) -> tuple[list[dict[str, str]], dict]:
    aliases = undergrad_aliases(profiles_2023_all)
    groups: dict[tuple[str, str, str], dict] = {}
    method_counts = Counter()
    identity_count = 0
    for (province, school_name, level), profile in sorted(profiles_2023_all.items()):
        if level != "대학원" or "일반대학원" in profile["school_kinds"]:
            continue
        identity_count += 1
        parent, method = resolve_parent_school(school_name, aliases)
        method_counts[method] += 1
        group_parent = parent or school_name
        key = (province, group_parent, method)
        group = groups.setdefault(
            key, {"departments": set(), "school_kinds": set(), "components": set()}
        )
        group["departments"].update(profile["departments"])
        group["school_kinds"].update(profile["school_kinds"])
        group["components"].add(school_name)
    rows = []
    for (province, parent, method), group in sorted(groups.items()):
        rows.append(
            {
                "province": province,
                "parent_school_name": parent if method != "unresolved_parent" else "",
                "parent_resolution_method": method,
                "graduate_school_identity_count": str(len(group["components"])),
                "department_count": str(len(group["departments"])),
                "graduate_school_kinds": "|".join(sorted(group["school_kinds"])),
                "component_school_names": "|".join(sorted(group["components"])),
            }
        )
    return rows, {
        "non_general_graduate_identity_count": identity_count,
        "parent_group_row_count": len(rows),
        "parent_resolution_method_counts": dict(sorted(method_counts.items())),
    }


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
        default=Path("data/processed/edss/restricted/derived/employment_2022_2023_reciprocal_review.csv"),
    )
    parser.add_argument(
        "--crosswalk-output",
        type=Path,
        default=Path("data/processed/edss/restricted/derived/employment_2022_2023_provisional_crosswalk.csv"),
    )
    parser.add_argument(
        "--parent-output",
        type=Path,
        default=Path("data/metadata/edss_employment_2023_non_general_graduate_parent_groups.csv"),
    )
    parser.add_argument(
        "--region-output",
        type=Path,
        default=Path("data/metadata/edss_employment_2022_2023_comparable_region_counts.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("data/metadata/edss_employment_2022_2023_provisional_crosswalk.json"),
    )
    args = parser.parse_args()

    source_2022 = args.raw_root / "취업통계/0001_학생인적취업정보_13299/0001_학생인적취업정보_2022.zip"
    source_2023 = args.raw_root / "취업통계/0001_학생인적취업정보_13300/0001_학생인적취업정보_2023.zip"
    profiles_2022 = base.load_2022_profiles(source_2022)
    profiles_2023_all = base.load_2023_profiles(source_2023)
    profiles_2023 = select_comparable_2023_profiles(profiles_2023_all)
    bridge = base.load_bridge(args.bridge)
    same_year_evidence = load_same_year_open_id_evidence(
        args.enrollment_candidates, args.graduate_candidates
    )
    review_rows, accepted_rows, region_rows, match_summary = build_reciprocal_matches(
        profiles_2022, profiles_2023, bridge, same_year_evidence, args.tolerance
    )
    parent_rows, parent_summary = build_parent_groups(profiles_2023_all)

    write_csv(args.review_output, REVIEW_FIELDS, review_rows)
    write_csv(args.crosswalk_output, CROSSWALK_FIELDS, accepted_rows)
    write_csv(args.parent_output, PARENT_FIELDS, parent_rows)
    write_csv(args.region_output, REGION_FIELDS, region_rows)

    summary = {
        "generated_at": base.utc_now(),
        "status": "provisional_review_required",
        "method": {
            "comparable_2023_populations": ["대학", "일반대학원"],
            "context": ["province", "university_or_general_graduate_scope"],
            "department_count_tolerance": args.tolerance,
            "accepted_rule": "unique reciprocal best plus exact department set of at least 3 or high overlap",
            "canonical_mapping_written": False,
        },
        "counts": {**match_summary, **parent_summary},
        "inputs": {
            "source_2022": {"path": str(source_2022), "sha256": base.sha256_file(source_2022)},
            "source_2023": {"path": str(source_2023), "sha256": base.sha256_file(source_2023)},
            "bridge": {"path": str(args.bridge), "sha256": base.sha256_file(args.bridge)},
            "enrollment_candidates": {
                "path": str(args.enrollment_candidates),
                "sha256": base.sha256_file(args.enrollment_candidates),
            },
            "graduate_candidates": {
                "path": str(args.graduate_candidates),
                "sha256": base.sha256_file(args.graduate_candidates),
            },
        },
        "outputs": {},
        "caveats": [
            "Accepted rows are provisional research evidence, not a canonical identity crosswalk.",
            "Only undergraduate institutions and general graduate schools are comparable at the 2022 grain.",
            "Professional and special graduate schools are parent-grouped for diagnosis but excluded from the one-to-one match.",
            "A 2023 OpenID remains a candidate from separate EDSS panels and is blank when no unique external candidate exists.",
        ],
    }
    for label, path, rows in (
        ("review", args.review_output, review_rows),
        ("crosswalk", args.crosswalk_output, accepted_rows),
        ("parent_groups", args.parent_output, parent_rows),
        ("region_counts", args.region_output, region_rows),
    ):
        summary["outputs"][label] = {
            "path": str(path), "row_count": len(rows), "sha256": base.sha256_file(path)
        }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
