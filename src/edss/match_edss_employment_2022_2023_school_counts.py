#!/usr/bin/env python3
"""Build review-only 2022 OpenID to 2023 school-name candidates.

The matcher follows a deliberately narrow adjacent-year hypothesis:

* compare institutions within the same province and university/graduate level;
* count distinct normalized department names for each 2022 OpenID and each
  2023 school name;
* keep pairs whose department counts differ by at most ``--tolerance``;
* rank those pairs by department-name overlap.

The output is evidence for review, not a canonical crosswalk.  Raw inputs are
read-only and no OpenID is written back to an EDSS panel.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REGION_COUNT_FIELDS = (
    "province",
    "level",
    "schools_2022",
    "schools_2023",
    "difference_2023_minus_2022",
)

CANDIDATE_FIELDS = (
    "school_identity_key",
    "province",
    "level",
    "school_name",
    "branches_2023",
    "school_kinds_2023",
    "department_count_2023",
    "candidate_rank",
    "candidate_open_id",
    "provinces_2022",
    "school_types_2022",
    "department_count_2022",
    "department_count_difference",
    "department_overlap_count",
    "department_union_count",
    "department_jaccard",
    "smaller_set_coverage",
    "count_tolerance_candidate_count",
    "top_score_tie_count",
    "selection_status",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", "", normalized).casefold()


def split_values(value: str) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in (value or "").split("|") if item.strip()}))


def normalize_level(value: str) -> str:
    return "대학원" if "대학원" in (value or "") else "대학"


def school_identity_key(province: str, school_name: str, level: str) -> str:
    payload = "\x1f".join((normalize_text(province), normalize_text(school_name), level))
    return "employment-school-2023-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nested_csv_rows(path: Path, encoding: str) -> Iterable[dict[str, str]]:
    with zipfile.ZipFile(path) as outer:
        inner_names = [name for name in outer.namelist() if name.lower().endswith(".zip")]
        if len(inner_names) != 1:
            raise RuntimeError(f"expected one inner ZIP in {path}, found {len(inner_names)}")
        inner_bytes = outer.read(inner_names[0])
    with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
        csv_names = [name for name in inner.namelist() if name.lower().endswith(".csv")]
        if len(csv_names) != 1:
            raise RuntimeError(f"expected one CSV in nested ZIP {path}, found {len(csv_names)}")
        with inner.open(csv_names[0]) as binary_handle:
            with io.TextIOWrapper(binary_handle, encoding=encoding, newline="") as text_handle:
                yield from csv.DictReader(text_handle)


def load_bridge(path: Path, year: str = "2022") -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["_panel_year"] == year]
    lookup = {row["개방ID"]: row for row in rows}
    if len(lookup) != len(rows):
        raise RuntimeError(f"bridge contains duplicate {year} OpenIDs")
    return lookup


def load_2022_profiles(path: Path, encoding: str = "cp949") -> dict[str, set[str]]:
    profiles: dict[str, set[str]] = defaultdict(set)
    for row in nested_csv_rows(path, encoding):
        open_id = (row.get("개방ID") or "").strip()
        department = normalize_text(row.get("학과명") or "")
        if not open_id or not department:
            raise RuntimeError("2022 source contains a blank OpenID or department")
        profiles[open_id].add(department)
    return dict(profiles)


def load_2023_profiles(path: Path, encoding: str = "cp949") -> dict[tuple[str, str, str], dict]:
    profiles: dict[tuple[str, str, str], dict] = {}
    for row in nested_csv_rows(path, encoding):
        province = (row.get("시도명") or "").strip()
        school_name = (row.get("학교명") or "").strip()
        level = (row.get("대학대학원구분명") or "").strip()
        department = normalize_text(row.get("학과명") or "")
        if not province or not school_name or level not in {"대학", "대학원"} or not department:
            raise RuntimeError("2023 source contains a blank or invalid school identity field")
        key = (province, school_name, level)
        profile = profiles.setdefault(key, {"departments": set(), "branches": set(), "school_kinds": set()})
        profile["departments"].add(department)
        profile["branches"].add((row.get("본분교명") or "").strip())
        profile["school_kinds"].add((row.get("학교종류명") or "").strip())
    return profiles


def candidate_metrics(left: set[str], right: set[str]) -> dict[str, float | int]:
    overlap = len(left & right)
    union = len(left | right)
    smaller = min(len(left), len(right))
    return {
        "department_count_difference": abs(len(left) - len(right)),
        "department_overlap_count": overlap,
        "department_union_count": union,
        "department_jaccard": overlap / union if union else 0.0,
        "smaller_set_coverage": overlap / smaller if smaller else 0.0,
    }


def rank_candidate_pairs(
    departments_2023: set[str],
    candidate_ids: Iterable[str],
    profiles_2022: dict[str, set[str]],
    tolerance: int,
) -> list[dict]:
    pairs = []
    for open_id in candidate_ids:
        metrics = candidate_metrics(profiles_2022[open_id], departments_2023)
        if metrics["department_count_difference"] > tolerance:
            continue
        pairs.append({"candidate_open_id": open_id, **metrics})
    pairs.sort(
        key=lambda row: (
            -row["department_overlap_count"],
            -row["department_jaccard"],
            -row["smaller_set_coverage"],
            row["department_count_difference"],
            row["candidate_open_id"],
        )
    )
    return pairs


def top_score_key(row: dict) -> tuple:
    return (
        row["department_overlap_count"],
        round(row["department_jaccard"], 12),
        round(row["smaller_set_coverage"], 12),
        -row["department_count_difference"],
    )


def preliminary_status(pairs: list[dict], departments_2023: set[str], profiles_2022: dict[str, set[str]]) -> str:
    if not pairs:
        return "no_count_tolerance_candidate"
    best = pairs[0]
    tie_count = sum(top_score_key(pair) == top_score_key(best) for pair in pairs)
    if tie_count > 1:
        return "ambiguous_top_score_tie"
    candidate_departments = profiles_2022[best["candidate_open_id"]]
    if candidate_departments == departments_2023:
        return "unique_exact_department_set"
    if (
        best["department_overlap_count"] >= 3
        and best["department_jaccard"] >= 0.80
        and best["smaller_set_coverage"] >= 0.90
    ):
        return "unique_high_overlap"
    return "unique_best_review_required"


def build_matches(
    profiles_2022: dict[str, set[str]],
    profiles_2023: dict[tuple[str, str, str], dict],
    bridge: dict[str, dict[str, str]],
    tolerance: int,
    max_candidates: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict]:
    context_index: dict[tuple[str, str], set[str]] = defaultdict(set)
    profile_context: dict[str, dict] = {}
    bridge_missing = []
    for open_id in profiles_2022:
        row = bridge.get(open_id)
        if row is None:
            bridge_missing.append(open_id)
            continue
        provinces = split_values(row.get("_0101_provinces", ""))
        school_types = split_values(row.get("_0101_school_types", ""))
        if not provinces or not school_types:
            bridge_missing.append(open_id)
            continue
        levels = {normalize_level(value) for value in school_types}
        profile_context[open_id] = {"provinces": provinces, "school_types": school_types, "levels": levels}
        for province in provinces:
            for level in levels:
                context_index[(province, level)].add(open_id)

    counts_2022 = Counter()
    for (province, level), ids in context_index.items():
        counts_2022[(province, level)] = len(ids)
    counts_2023 = Counter((province, level) for province, _school, level in profiles_2023)
    region_rows = [
        {
            "province": province,
            "level": level,
            "schools_2022": str(counts_2022[(province, level)]),
            "schools_2023": str(counts_2023[(province, level)]),
            "difference_2023_minus_2022": str(counts_2023[(province, level)] - counts_2022[(province, level)]),
        }
        for province, level in sorted(set(counts_2022) | set(counts_2023))
    ]

    identities = []
    for (province, school_name, level), profile in sorted(profiles_2023.items()):
        pairs = rank_candidate_pairs(
            profile["departments"],
            context_index.get((province, level), set()),
            profiles_2022,
            tolerance,
        )
        status = preliminary_status(pairs, profile["departments"], profiles_2022)
        tie_count = 0 if not pairs else sum(top_score_key(pair) == top_score_key(pairs[0]) for pair in pairs)
        identities.append(
            {
                "key": (province, school_name, level),
                "identity_key": school_identity_key(province, school_name, level),
                "profile": profile,
                "pairs": pairs,
                "status": status,
                "tie_count": tie_count,
            }
        )

    selected_by_open_id: dict[str, list[dict]] = defaultdict(list)
    for identity in identities:
        if identity["status"] in {"unique_exact_department_set", "unique_high_overlap"}:
            selected_by_open_id[identity["pairs"][0]["candidate_open_id"]].append(identity)
    reverse_conflicts = {
        open_id: values for open_id, values in selected_by_open_id.items() if len(values) > 1
    }
    for values in reverse_conflicts.values():
        for identity in values:
            identity["status"] = "reverse_open_id_conflict"

    candidate_rows: list[dict[str, str]] = []
    for identity in identities:
        province, school_name, level = identity["key"]
        profile = identity["profile"]
        pairs = identity["pairs"]
        emitted_pairs = pairs[:max_candidates] if pairs else [None]
        for rank, pair in enumerate(emitted_pairs, start=1):
            open_id = pair["candidate_open_id"] if pair else ""
            context = profile_context.get(open_id, {})
            candidate_rows.append(
                {
                    "school_identity_key": identity["identity_key"],
                    "province": province,
                    "level": level,
                    "school_name": school_name,
                    "branches_2023": "|".join(sorted(profile["branches"])),
                    "school_kinds_2023": "|".join(sorted(profile["school_kinds"])),
                    "department_count_2023": str(len(profile["departments"])),
                    "candidate_rank": str(rank) if pair else "",
                    "candidate_open_id": open_id,
                    "provinces_2022": "|".join(context.get("provinces", ())),
                    "school_types_2022": "|".join(context.get("school_types", ())),
                    "department_count_2022": str(len(profiles_2022[open_id])) if open_id else "",
                    "department_count_difference": str(pair["department_count_difference"]) if pair else "",
                    "department_overlap_count": str(pair["department_overlap_count"]) if pair else "",
                    "department_union_count": str(pair["department_union_count"]) if pair else "",
                    "department_jaccard": f'{pair["department_jaccard"]:.6f}' if pair else "",
                    "smaller_set_coverage": f'{pair["smaller_set_coverage"]:.6f}' if pair else "",
                    "count_tolerance_candidate_count": str(len(pairs)),
                    "top_score_tie_count": str(identity["tie_count"]),
                    "selection_status": identity["status"],
                }
            )

    identity_status_counts = Counter(identity["status"] for identity in identities)
    level_status_counts: dict[str, Counter] = defaultdict(Counter)
    for identity in identities:
        level_status_counts[identity["key"][2]][identity["status"]] += 1
    region_level_counts = {}
    for level in sorted({row["level"] for row in region_rows}):
        level_rows = [row for row in region_rows if row["level"] == level]
        differences = [int(row["difference_2023_minus_2022"]) for row in level_rows]
        region_level_counts[level] = {
            "region_count": len(level_rows),
            "school_appearances_2022": sum(int(row["schools_2022"]) for row in level_rows),
            "school_identities_2023": sum(int(row["schools_2023"]) for row in level_rows),
            "regions_with_absolute_difference_at_most_tolerance": sum(
                abs(value) <= tolerance for value in differences
            ),
            "maximum_absolute_difference": max(map(abs, differences), default=0),
        }
    identities_with_candidates = sum(bool(identity["pairs"]) for identity in identities)
    total_candidate_pairs = sum(len(identity["pairs"]) for identity in identities)
    truncated_identities = sum(len(identity["pairs"]) > max_candidates for identity in identities)
    summary = {
        "generated_at": utc_now(),
        "status": "review_required",
        "method": {
            "context": ["province", "university_or_graduate_level"],
            "school_unit_2022": "개방ID",
            "school_unit_2023": "(시도명, 학교명, 대학대학원구분명)",
            "size_measure": "distinct normalized 학과명 count",
            "department_count_tolerance": tolerance,
            "ranking": "department overlap, Jaccard, smaller-set coverage, then count difference",
            "max_candidates_written_per_2023_identity": max_candidates,
            "canonical_mapping_written": False,
        },
        "counts": {
            "open_id_count_2022": len(profiles_2022),
            "school_identity_count_2023": len(profiles_2023),
            "bridge_missing_open_id_count": len(bridge_missing),
            "candidate_output_row_count": len(candidate_rows),
            "count_tolerance_pair_count_before_output_cap": total_candidate_pairs,
            "identities_with_count_tolerance_candidate": identities_with_candidates,
            "identities_without_count_tolerance_candidate": len(identities) - identities_with_candidates,
            "identities_truncated_to_output_cap": truncated_identities,
            "identity_status_counts": dict(sorted(identity_status_counts.items())),
            "level_status_counts": {
                level: dict(sorted(counts.items())) for level, counts in sorted(level_status_counts.items())
            },
            "region_level_school_counts": region_level_counts,
            "reverse_conflicting_open_id_count": len(reverse_conflicts),
        },
        "caveats": [
            "2022 OpenIDs can span more than one province; they are eligible in every recorded 2022 province.",
            "Graduate-school names are much more granular in 2023 than employment OpenIDs are in 2022, so graduate matching can be many-to-one.",
            "A count-tolerance candidate is not a confirmed identity; department-name overlap and reverse conflicts must be reviewed.",
        ],
    }
    return candidate_rows, region_rows, summary


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
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument(
        "--candidate-output",
        type=Path,
        default=Path("data/processed/edss/restricted/derived/employment_2022_2023_department_count_candidates.csv"),
    )
    parser.add_argument(
        "--region-count-output",
        type=Path,
        default=Path("data/metadata/edss_employment_2022_2023_region_school_counts.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("data/metadata/edss_employment_2022_2023_department_count_match.json"),
    )
    args = parser.parse_args()
    if args.tolerance < 0 or args.max_candidates < 1:
        raise SystemExit("tolerance must be nonnegative and max-candidates must be positive")

    source_2022 = args.raw_root / "취업통계/0001_학생인적취업정보_13299/0001_학생인적취업정보_2022.zip"
    source_2023 = args.raw_root / "취업통계/0001_학생인적취업정보_13300/0001_학생인적취업정보_2023.zip"
    profiles_2022 = load_2022_profiles(source_2022)
    profiles_2023 = load_2023_profiles(source_2023)
    bridge = load_bridge(args.bridge)
    candidate_rows, region_rows, summary = build_matches(
        profiles_2022,
        profiles_2023,
        bridge,
        tolerance=args.tolerance,
        max_candidates=args.max_candidates,
    )
    summary["inputs"] = {
        "source_2022": {"path": str(source_2022), "sha256": sha256_file(source_2022)},
        "source_2023": {"path": str(source_2023), "sha256": sha256_file(source_2023)},
        "bridge": {"path": str(args.bridge), "sha256": sha256_file(args.bridge)},
    }
    write_csv(args.candidate_output, CANDIDATE_FIELDS, candidate_rows)
    write_csv(args.region_count_output, REGION_COUNT_FIELDS, region_rows)
    summary["outputs"] = {
        "candidate_output": {
            "path": str(args.candidate_output),
            "row_count": len(candidate_rows),
            "sha256": sha256_file(args.candidate_output),
        },
        "region_count_output": {
            "path": str(args.region_count_output),
            "row_count": len(region_rows),
            "sha256": sha256_file(args.region_count_output),
        },
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
