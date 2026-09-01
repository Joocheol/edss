#!/usr/bin/env python3
"""Cross-check the six priority EDSS orphan school-year identifiers.

The script never guesses a school name from an anonymized EDSS ``개방ID``.
Instead, it extracts the source rows, compares department sets and complete
row signatures against identifiers that do join to the 0101 base panel, and
emits inspectable candidate evidence for subsequent official-history review.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path


TARGETS = {
    ("2009", "4124740779"): "open_id_absent_all_years",
    ("2009", "5814962685"): "open_id_absent_all_years",
    ("2009", "6566148042"): "open_id_absent_all_years",
    ("2015", "5831784427"): "internal_base_gap",
    ("2016", "9080059170"): "open_id_absent_all_years",
    ("2019", "2957261025"): "open_id_absent_all_years",
}

ZIP_NAMES = {
    "0101": "0101. 고등교육학교개황(09-25).zip",
    "0316": "0316. 중도탈락학생현황(09-25).zip",
    "0502": "0502. 전체교원대비전임교원현황(09-25).zip",
    "1016": "1016. 장학금수혜현황(09-25).zip",
}


def iter_zip_csv(path: Path):
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir() or not member.filename.lower().endswith(".csv"):
                continue
            with archive.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="cp949", newline="")
                yield from csv.DictReader(text)


def load_base(path: Path):
    by_year_id: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    years_by_id: dict[str, set[str]] = defaultdict(set)
    for row in iter_zip_csv(path):
        key = (row["조사년도"], row["개방ID"])
        by_year_id[key].append(row)
        years_by_id[row["개방ID"]].add(row["조사년도"])
    return by_year_id, years_by_id


def comparable_signature(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
    excluded = {"개방ID"}
    return tuple(sorted((key, value) for key, value in row.items() if key not in excluded))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--downloads",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "raw" / "source_downloads",
        help="Directory containing the four EDSS source ZIPs",
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()

    paths = {code: args.downloads / name for code, name in ZIP_NAMES.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise SystemExit("Missing EDSS source ZIPs: " + ", ".join(missing))

    base, base_years = load_base(paths["0101"])

    faculty_by_year_id: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    faculty_signature_index: dict[tuple[str, tuple[tuple[str, str], ...]], set[str]] = defaultdict(set)
    department_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in iter_zip_csv(paths["0502"]):
        key = (row["조사년도"], row["개방ID"])
        faculty_by_year_id[key].append(row)
        department_sets[key].add(row["학과한글명"])
        faculty_signature_index[(row["조사년도"], comparable_signature(row))].add(row["개방ID"])

    source_rows: dict[tuple[str, str], dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for code in ("0316", "1016"):
        for row in iter_zip_csv(paths[code]):
            key = (row["조사년도"], row["개방ID"])
            if key in TARGETS:
                source_rows[key][code].append(row)

    results = []
    candidate_rows = []
    for (year, open_id), classification in TARGETS.items():
        faculty_rows = faculty_by_year_id[(year, open_id)]
        departments = sorted(department_sets[(year, open_id)])
        exact_row_match_ids: set[str] = set()
        for row in faculty_rows:
            exact_row_match_ids.update(faculty_signature_index[(year, comparable_signature(row))])
        exact_row_match_ids.discard(open_id)

        overlap_candidates = []
        target_set = set(departments)
        for (candidate_year, candidate_id), candidate_set in department_sets.items():
            if candidate_year != year or candidate_id == open_id:
                continue
            overlap = target_set & candidate_set
            if not overlap:
                continue
            union = target_set | candidate_set
            overlap_candidates.append(
                {
                    "candidate_open_id": candidate_id,
                    "overlap_count": len(overlap),
                    "target_department_count": len(target_set),
                    "candidate_department_count": len(candidate_set),
                    "jaccard": round(len(overlap) / len(union), 6),
                    "overlap_departments": sorted(overlap),
                    "exact_row_match": candidate_id in exact_row_match_ids,
                    "base_same_year": (year, candidate_id) in base,
                }
            )
        overlap_candidates.sort(
            key=lambda row: (
                row["exact_row_match"],
                row["overlap_count"],
                row["jaccard"],
                row["base_same_year"],
            ),
            reverse=True,
        )

        top_candidates = overlap_candidates[:20]
        for candidate in top_candidates:
            attrs = base.get((year, candidate["candidate_open_id"]), [])
            candidate_rows.append(
                {
                    "year": year,
                    "target_open_id": open_id,
                    **candidate,
                    "base_school_types": "|".join(sorted({r["학교구분명"] for r in attrs})),
                    "base_regions": "|".join(sorted({r["시도명"] for r in attrs})),
                    "base_localities": "|".join(sorted({r["지역명"] for r in attrs})),
                    "base_program_types": "|".join(sorted({r["학제유형명"] for r in attrs})),
                }
            )

        target_base_rows = [
            row
            for (candidate_year, candidate_id), rows in base.items()
            if candidate_id == open_id
            for row in rows
        ]
        results.append(
            {
                "year": year,
                "open_id": open_id,
                "classification": classification,
                "departments": departments,
                "faculty_source_row_count": len(faculty_rows),
                "other_source_row_counts": {
                    code: len(rows) for code, rows in sorted(source_rows[(year, open_id)].items())
                },
                "base_observed_years": sorted(base_years.get(open_id, set())),
                "base_attributes": {
                    "school_types": sorted({row["학교구분명"] for row in target_base_rows}),
                    "regions": sorted({row["시도명"] for row in target_base_rows}),
                    "localities": sorted({row["지역명"] for row in target_base_rows}),
                    "branch_types": sorted({row["본분교명"] for row in target_base_rows}),
                    "program_types": sorted({row["학제유형명"] for row in target_base_rows}),
                },
                "exact_row_match_ids": sorted(exact_row_match_ids),
                "top_department_overlap_candidates": top_candidates,
            }
        )

    payload = {
        # Keep generated metadata portable and avoid publishing local home paths.
        "source_files": {code: path.name for code, path in paths.items()},
        "targets": results,
        "method_note": (
            "Candidate matches are structural evidence only. EDSS open IDs are not mapped "
            "to school names unless an independent official source confirms the identity."
        ),
    }

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "year",
            "target_open_id",
            "candidate_open_id",
            "overlap_count",
            "target_department_count",
            "candidate_department_count",
            "jaccard",
            "overlap_departments",
            "exact_row_match",
            "base_same_year",
            "base_school_types",
            "base_regions",
            "base_localities",
            "base_program_types",
        ]
        with args.output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in candidate_rows:
                row = dict(row)
                row["overlap_departments"] = "|".join(row["overlap_departments"])
                writer.writerow(row)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
