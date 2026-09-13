#!/usr/bin/env python3
"""Validate school attributes and build stratified EDSS employment trends.

The restricted joined school-cohort view is read without modification.  School
type and province are validated before use.  Province lists spanning multiple
campuses are kept in an explicit ``복수시도`` category rather than being
assigned to an arbitrary province.  Outputs contain aggregates only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path("data/processed/edss/restricted/edss_all.duckdb")
DEFAULT_QUALITY_CSV = Path(
    "data/metadata/edss_employment_stratified_attribute_quality.csv"
)
DEFAULT_STABILITY_CSV = Path(
    "data/metadata/edss_employment_stratified_attribute_stability.csv"
)
DEFAULT_TRENDS_CSV = Path(
    "data/metadata/edss_employment_stratified_trends.csv"
)
DEFAULT_OUTPUT_JSON = Path(
    "data/metadata/edss_employment_stratified_trends.json"
)
SOURCE_VIEW = "analysis.school_year_core_with_employment_cohort_2010_2020"
EXPECTED_COHORTS = tuple(str(year) for year in range(2010, 2021))
EXPECTED_SOURCE_ROW_COUNT = 5969
MISSING_STRATUM = "속성없음"
MULTIPLE_PROVINCES_STRATUM = "복수시도"
INTERVALS = {
    "june_1": {
        "name": "june_1_2010_2013",
        "cohorts": tuple(str(year) for year in range(2010, 2014)),
    },
    "december_31": {
        "name": "december_31_2014_2020",
        "cohorts": tuple(str(year) for year in range(2014, 2021)),
    },
}

SOURCE_FIELDS = (
    "employment_cohort_year",
    "employment_source_panel_year",
    "employment_reference_date",
    "employment_reference_date_basis",
    "open_id",
    "school_types",
    "school_type_count",
    "provinces",
    "province_count",
    "source_record_count",
    "reported_graduate_count",
    "reported_employed_count",
)

QUALITY_FIELDS = (
    "employment_cohort_year",
    "employment_reference_date_basis",
    "comparison_interval",
    "attribute",
    "source_field",
    "total_school_count",
    "matched_attribute_school_count",
    "missing_attribute_school_count",
    "multi_value_school_count",
    "attribute_school_coverage_share",
    "total_reported_graduate_count",
    "matched_attribute_reported_graduate_count",
    "attribute_graduate_coverage_share",
    "distinct_stratum_count",
)

STABILITY_FIELDS = (
    "attribute",
    "comparison_interval",
    "first_cohort_year",
    "last_cohort_year",
    "interval_balanced_school_count",
    "complete_attribute_balanced_school_count",
    "stable_attribute_balanced_school_count",
    "unstable_attribute_balanced_school_count",
    "stable_attribute_school_share",
    "attribute_transition_count",
    "stable_attribute_stratum_count",
    "minimum_stable_attribute_graduate_coverage_share",
)

TREND_FIELDS = (
    "attribute",
    "stratum",
    "employment_cohort_year",
    "employment_source_panel_year",
    "employment_reference_date",
    "employment_reference_date_basis",
    "comparison_interval",
    "previous_comparable_cohort_year",
    "all_available_school_count",
    "stratum_balanced_school_count",
    "stratum_balanced_school_coverage_share",
    "all_available_source_record_count",
    "stratum_balanced_source_record_count",
    "all_available_reported_graduate_count",
    "stratum_balanced_reported_graduate_count",
    "stratum_balanced_graduate_coverage_share",
    "all_available_reported_employed_count",
    "stratum_balanced_reported_employed_count",
    "all_available_reported_employed_share_of_graduates",
    "stratum_balanced_reported_employed_share_of_graduates",
    "balanced_minus_all_reported_employed_share_pp",
    "all_available_share_change_pp_from_previous_comparable_cohort",
    "stratum_balanced_share_change_pp_from_previous_comparable_cohort",
    "balanced_minus_all_yoy_change_pp",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_duckdb():
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "duckdb is required; run with `uv run --with duckdb==1.4.1 python ...`"
        ) from exc
    return duckdb


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator < 0 or numerator < 0:
        raise RuntimeError("ratio inputs must be nonnegative")
    if denominator == 0:
        return None
    return round(numerator / denominator, 9)


def comparison_interval(basis: str) -> str:
    if basis not in INTERVALS:
        raise RuntimeError(f"unexpected reference-date basis: {basis!r}")
    return str(INTERVALS[basis]["name"])


def query_source_rows(connection) -> list[dict[str, object]]:
    rows = connection.execute(
        f"""
        SELECT
            _panel_year,
            employment_source_panel_year,
            employment_reference_date,
            employment_reference_date_basis,
            개방ID,
            school_types,
            school_type_count,
            provinces,
            province_count,
            employment_source_record_count,
            employment_reported_graduate_count,
            employment_reported_employed_count
        FROM {SOURCE_VIEW}
        WHERE _employment_cohort_exists = 'true'
        ORDER BY _panel_year, 개방ID
        """
    ).fetchall()
    return [dict(zip(SOURCE_FIELDS, row)) for row in rows]


def validate_source_rows(rows: list[dict[str, object]]) -> dict[str, object]:
    keys = [
        (str(row["employment_cohort_year"]), str(row["open_id"])) for row in rows
    ]
    cohorts = tuple(sorted({year for year, _ in keys}))
    blank_open_ids = sum(not open_id.strip() for _, open_id in keys)
    duplicate_keys = len(keys) - len(set(keys))
    negative_measure_rows = sum(
        int(row[field]) < 0
        for row in rows
        for field in (
            "source_record_count",
            "reported_graduate_count",
            "reported_employed_count",
        )
    )
    profile = {
        "row_count": len(rows),
        "blank_open_id_row_count": blank_open_ids,
        "duplicate_school_cohort_key_count": duplicate_keys,
        "negative_measure_value_count": negative_measure_rows,
        "cohort_count": len(cohorts),
        "first_cohort_year": cohorts[0] if cohorts else None,
        "last_cohort_year": cohorts[-1] if cohorts else None,
    }
    expected = {
        "row_count": EXPECTED_SOURCE_ROW_COUNT,
        "blank_open_id_row_count": 0,
        "duplicate_school_cohort_key_count": 0,
        "negative_measure_value_count": 0,
        "cohort_count": 11,
        "first_cohort_year": "2010",
        "last_cohort_year": "2020",
    }
    if profile != expected:
        raise RuntimeError(
            f"unexpected source mart quality profile: expected {expected}, got {profile}"
        )
    if cohorts != EXPECTED_COHORTS:
        raise RuntimeError(f"unexpected cohort sequence: {cohorts}")
    for row in rows:
        year = str(row["employment_cohort_year"])
        basis = str(row["employment_reference_date_basis"])
        if year not in INTERVALS.get(basis, {}).get("cohorts", ()):
            raise RuntimeError(f"cohort {year} has unexpected basis {basis!r}")
    return profile


def attribute_value(row: dict[str, object], attribute: str) -> tuple[str, bool, bool]:
    if attribute == "school_type":
        raw = str(row.get("school_types") or "").strip()
        count = int(row.get("school_type_count") or 0)
        if not raw or count == 0:
            return MISSING_STRATUM, True, False
        if count > 1:
            return "복수학교유형", False, True
        return raw, False, False
    if attribute == "province":
        raw = str(row.get("provinces") or "").strip()
        count = int(row.get("province_count") or 0)
        if not raw or count == 0:
            return MISSING_STRATUM, True, False
        if count > 1:
            return MULTIPLE_PROVINCES_STRATUM, False, True
        return raw, False, False
    raise ValueError(f"unsupported attribute: {attribute}")


def build_quality_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    by_year: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_year[str(row["employment_cohort_year"])].append(row)

    for year in EXPECTED_COHORTS:
        year_rows = by_year[year]
        basis = str(year_rows[0]["employment_reference_date_basis"])
        for attribute, source_field in (
            ("school_type", "school_types"),
            ("province", "provinces"),
        ):
            classified = [attribute_value(row, attribute) for row in year_rows]
            missing_flags = [value[1] for value in classified]
            matched_rows = [
                row for row, missing in zip(year_rows, missing_flags) if not missing
            ]
            total_graduates = sum(
                int(row["reported_graduate_count"]) for row in year_rows
            )
            matched_graduates = sum(
                int(row["reported_graduate_count"]) for row in matched_rows
            )
            output.append(
                {
                    "employment_cohort_year": year,
                    "employment_reference_date_basis": basis,
                    "comparison_interval": comparison_interval(basis),
                    "attribute": attribute,
                    "source_field": source_field,
                    "total_school_count": len(year_rows),
                    "matched_attribute_school_count": len(matched_rows),
                    "missing_attribute_school_count": sum(missing_flags),
                    "multi_value_school_count": sum(value[2] for value in classified),
                    "attribute_school_coverage_share": ratio(
                        len(matched_rows), len(year_rows)
                    ),
                    "total_reported_graduate_count": total_graduates,
                    "matched_attribute_reported_graduate_count": matched_graduates,
                    "attribute_graduate_coverage_share": ratio(
                        matched_graduates, total_graduates
                    ),
                    "distinct_stratum_count": len({value[0] for value in classified}),
                }
            )
    return output


def interval_balanced_ids(
    rows: list[dict[str, object]],
) -> dict[str, set[str]]:
    years_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        basis = str(row["employment_reference_date_basis"])
        interval = comparison_interval(basis)
        years_by_key[(interval, str(row["open_id"]))].add(
            str(row["employment_cohort_year"])
        )
    output: dict[str, set[str]] = defaultdict(set)
    for (interval, open_id), years in years_by_key.items():
        expected = next(
            set(definition["cohorts"])
            for definition in INTERVALS.values()
            if definition["name"] == interval
        )
        if years == expected:
            output[interval].add(open_id)
    return output


def build_stability_rows(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    balanced = interval_balanced_ids(rows)
    indexed = {
        (comparison_interval(str(row["employment_reference_date_basis"])),
         str(row["open_id"]),
         str(row["employment_cohort_year"])): row
        for row in rows
    }
    output: list[dict[str, object]] = []
    for definition in INTERVALS.values():
        interval = str(definition["name"])
        cohorts = tuple(str(year) for year in definition["cohorts"])
        ids = balanced[interval]
        for attribute in ("school_type", "province"):
            complete: set[str] = set()
            stable: set[str] = set()
            transitions = 0
            stable_strata: set[str] = set()
            for open_id in ids:
                values = [
                    attribute_value(indexed[(interval, open_id, year)], attribute)[0]
                    for year in cohorts
                ]
                if MISSING_STRATUM not in values:
                    complete.add(open_id)
                    transitions += sum(
                        current != previous
                        for previous, current in zip(values, values[1:])
                    )
                    if len(set(values)) == 1:
                        stable.add(open_id)
                        stable_strata.add(values[0])
            annual_coverage: list[float] = []
            for year in cohorts:
                total = sum(
                    int(indexed[(interval, open_id, year)]["reported_graduate_count"])
                    for open_id in ids
                )
                stable_total = sum(
                    int(indexed[(interval, open_id, year)]["reported_graduate_count"])
                    for open_id in stable
                )
                annual_coverage.append(float(ratio(stable_total, total) or 0))
            output.append(
                {
                    "attribute": attribute,
                    "comparison_interval": interval,
                    "first_cohort_year": cohorts[0],
                    "last_cohort_year": cohorts[-1],
                    "interval_balanced_school_count": len(ids),
                    "complete_attribute_balanced_school_count": len(complete),
                    "stable_attribute_balanced_school_count": len(stable),
                    "unstable_attribute_balanced_school_count": len(complete - stable),
                    "stable_attribute_school_share": ratio(len(stable), len(ids)),
                    "attribute_transition_count": transitions,
                    "stable_attribute_stratum_count": len(stable_strata),
                    "minimum_stable_attribute_graduate_coverage_share": min(
                        annual_coverage
                    ),
                }
            )
    return sorted(output, key=lambda row: (row["attribute"], row["comparison_interval"]))


def build_stratified_rows(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    classified: list[dict[str, object]] = []
    for source in rows:
        interval = comparison_interval(str(source["employment_reference_date_basis"]))
        for attribute in ("school_type", "province"):
            stratum, _, _ = attribute_value(source, attribute)
            classified.append(
                {
                    **source,
                    "attribute": attribute,
                    "stratum": stratum,
                    "comparison_interval": interval,
                }
            )

    years_by_membership: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in classified:
        if row["stratum"] == MISSING_STRATUM:
            continue
        key = (
            str(row["attribute"]),
            str(row["stratum"]),
            str(row["comparison_interval"]),
            str(row["open_id"]),
        )
        years_by_membership[key].add(str(row["employment_cohort_year"]))
    balanced_memberships: set[tuple[str, str, str, str]] = set()
    for key, years in years_by_membership.items():
        interval = key[2]
        expected = next(
            set(definition["cohorts"])
            for definition in INTERVALS.values()
            if definition["name"] == interval
        )
        if years == expected:
            balanced_memberships.add(key)

    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in classified:
        grouped[
            (
                str(row["attribute"]),
                str(row["stratum"]),
                str(row["employment_cohort_year"]),
            )
        ].append(row)

    aggregates: dict[tuple[str, str, str], dict[str, object]] = {}
    for key, group in grouped.items():
        attribute, stratum, year = key
        balanced_rows = [
            row
            for row in group
            if (
                attribute,
                stratum,
                str(row["comparison_interval"]),
                str(row["open_id"]),
            )
            in balanced_memberships
        ]
        first = group[0]
        all_graduates = sum(int(row["reported_graduate_count"]) for row in group)
        balanced_graduates = sum(
            int(row["reported_graduate_count"]) for row in balanced_rows
        )
        all_employed = sum(int(row["reported_employed_count"]) for row in group)
        balanced_employed = sum(
            int(row["reported_employed_count"]) for row in balanced_rows
        )
        all_share = ratio(all_employed, all_graduates)
        balanced_share = ratio(balanced_employed, balanced_graduates)
        aggregates[key] = {
            "attribute": attribute,
            "stratum": stratum,
            "employment_cohort_year": year,
            "employment_source_panel_year": first["employment_source_panel_year"],
            "employment_reference_date": first["employment_reference_date"],
            "employment_reference_date_basis": first[
                "employment_reference_date_basis"
            ],
            "comparison_interval": first["comparison_interval"],
            "all_available_school_count": len(group),
            "stratum_balanced_school_count": len(balanced_rows),
            "stratum_balanced_school_coverage_share": ratio(
                len(balanced_rows), len(group)
            ),
            "all_available_source_record_count": sum(
                int(row["source_record_count"]) for row in group
            ),
            "stratum_balanced_source_record_count": sum(
                int(row["source_record_count"]) for row in balanced_rows
            ),
            "all_available_reported_graduate_count": all_graduates,
            "stratum_balanced_reported_graduate_count": balanced_graduates,
            "stratum_balanced_graduate_coverage_share": ratio(
                balanced_graduates, all_graduates
            ),
            "all_available_reported_employed_count": all_employed,
            "stratum_balanced_reported_employed_count": balanced_employed,
            "all_available_reported_employed_share_of_graduates": all_share,
            "stratum_balanced_reported_employed_share_of_graduates": balanced_share,
            "balanced_minus_all_reported_employed_share_pp": (
                round((balanced_share - all_share) * 100, 6)
                if balanced_share is not None and all_share is not None
                else None
            ),
        }

    output: list[dict[str, object]] = []
    for key in sorted(aggregates):
        row = aggregates[key]
        attribute, stratum, year = key
        interval = str(row["comparison_interval"])
        cohorts = next(
            tuple(definition["cohorts"])
            for definition in INTERVALS.values()
            if definition["name"] == interval
        )
        index = cohorts.index(year)
        previous_year = cohorts[index - 1] if index > 0 else None
        previous = (
            aggregates.get((attribute, stratum, previous_year))
            if previous_year is not None
            else None
        )
        row["previous_comparable_cohort_year"] = previous_year if previous else None
        all_share = row["all_available_reported_employed_share_of_graduates"]
        balanced_share = row[
            "stratum_balanced_reported_employed_share_of_graduates"
        ]
        previous_all_share = (
            previous["all_available_reported_employed_share_of_graduates"]
            if previous
            else None
        )
        previous_balanced_share = (
            previous["stratum_balanced_reported_employed_share_of_graduates"]
            if previous
            else None
        )
        all_change = (
            round((float(all_share) - float(previous_all_share)) * 100, 6)
            if all_share is not None and previous_all_share is not None
            else None
        )
        balanced_change = (
            round((float(balanced_share) - float(previous_balanced_share)) * 100, 6)
            if balanced_share is not None and previous_balanced_share is not None
            else None
        )
        row[
            "all_available_share_change_pp_from_previous_comparable_cohort"
        ] = all_change
        row[
            "stratum_balanced_share_change_pp_from_previous_comparable_cohort"
        ] = balanced_change
        row["balanced_minus_all_yoy_change_pp"] = (
            round(balanced_change - all_change, 6)
            if balanced_change is not None and all_change is not None
            else None
        )
        output.append({field: row.get(field) for field in TREND_FIELDS})
    return output


def validate_outputs(
    source_rows: list[dict[str, object]],
    trends: list[dict[str, object]],
) -> dict[str, object]:
    keys = [
        (str(row["attribute"]), str(row["stratum"]), str(row["employment_cohort_year"]))
        for row in trends
    ]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate attribute-stratum-cohort output key")

    source_totals: dict[str, tuple[int, int, int, int]] = {}
    for year in EXPECTED_COHORTS:
        selected = [
            row for row in source_rows if str(row["employment_cohort_year"]) == year
        ]
        source_totals[year] = (
            len(selected),
            sum(int(row["source_record_count"]) for row in selected),
            sum(int(row["reported_graduate_count"]) for row in selected),
            sum(int(row["reported_employed_count"]) for row in selected),
        )
    for attribute in ("school_type", "province"):
        for year in EXPECTED_COHORTS:
            selected = [
                row
                for row in trends
                if row["attribute"] == attribute
                and row["employment_cohort_year"] == year
            ]
            totals = (
                sum(int(row["all_available_school_count"]) for row in selected),
                sum(int(row["all_available_source_record_count"]) for row in selected),
                sum(
                    int(row["all_available_reported_graduate_count"])
                    for row in selected
                ),
                sum(
                    int(row["all_available_reported_employed_count"])
                    for row in selected
                ),
            )
            if totals != source_totals[year]:
                raise RuntimeError(
                    f"{attribute} strata do not reconcile for {year}: "
                    f"expected {source_totals[year]}, got {totals}"
                )
    return {
        "unique_attribute_stratum_cohort_keys": True,
        "school_type_strata_reconcile_to_source_by_cohort": True,
        "province_strata_reconcile_to_source_by_cohort": True,
        "interval_start_changes_are_null": all(
            row["all_available_share_change_pp_from_previous_comparable_cohort"]
            is None
            for row in trends
            if row["employment_cohort_year"] in ("2010", "2014")
        ),
    }


def csv_value(value: object) -> object:
    return "" if value is None else value


def atomic_write_csv(
    path: Path, records: list[dict[str, object]], fields: tuple[str, ...]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in records:
            writer.writerow({field: csv_value(row.get(field)) for field in fields})
    temporary.replace(path)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def change_2016_2020(
    trends: list[dict[str, object]], attribute: str
) -> list[dict[str, object]]:
    indexed = {
        (str(row["stratum"]), str(row["employment_cohort_year"])): row
        for row in trends
        if row["attribute"] == attribute
    }
    output: list[dict[str, object]] = []
    for stratum in sorted({key[0] for key in indexed}):
        start = indexed.get((stratum, "2016"))
        end = indexed.get((stratum, "2020"))
        if not start or not end:
            continue
        all_start = start["all_available_reported_employed_share_of_graduates"]
        all_end = end["all_available_reported_employed_share_of_graduates"]
        balanced_start = start[
            "stratum_balanced_reported_employed_share_of_graduates"
        ]
        balanced_end = end[
            "stratum_balanced_reported_employed_share_of_graduates"
        ]
        output.append(
            {
                "stratum": stratum,
                "all_available_change_pp": (
                    round((float(all_end) - float(all_start)) * 100, 6)
                    if all_start is not None and all_end is not None
                    else None
                ),
                "stratum_balanced_change_pp": (
                    round((float(balanced_end) - float(balanced_start)) * 100, 6)
                    if balanced_start is not None and balanced_end is not None
                    else None
                ),
                "balanced_school_count": int(
                    start["stratum_balanced_school_count"]
                ),
                "minimum_endpoint_balanced_graduate_coverage_share": min(
                    float(start["stratum_balanced_graduate_coverage_share"] or 0),
                    float(end["stratum_balanced_graduate_coverage_share"] or 0),
                ),
            }
        )
    return output


def build_summary(
    source_profile: dict[str, object],
    quality: list[dict[str, object]],
    stability: list[dict[str, object]],
    trends: list[dict[str, object]],
    validation: dict[str, object],
    database: Path,
    quality_csv: Path,
    stability_csv: Path,
    trends_csv: Path,
) -> dict[str, object]:
    missing_by_attribute = {
        attribute: {
            "school_cohort_count": sum(
                int(row["missing_attribute_school_count"])
                for row in quality
                if row["attribute"] == attribute
            ),
            "reported_graduate_count": sum(
                int(row["total_reported_graduate_count"])
                - int(row["matched_attribute_reported_graduate_count"])
                for row in quality
                if row["attribute"] == attribute
            ),
        }
        for attribute in ("school_type", "province")
    }
    headline = {
        attribute: change_2016_2020(trends, attribute)
        for attribute in ("school_type", "province")
    }
    comparable_headline = [
        row
        for rows in headline.values()
        for row in rows
        if row["all_available_change_pp"] is not None
        and row["stratum_balanced_change_pp"] is not None
    ]
    direction_agreement = sum(
        (float(row["all_available_change_pp"]) > 0)
        == (float(row["stratum_balanced_change_pp"]) > 0)
        for row in comparable_headline
    )
    return {
        "version": 1,
        "generated_at": utc_now(),
        "status": "share_with_caveats",
        "question": (
            "How do reported-employed descriptive shares vary by school type and "
            "province, and do 2016-to-2020 directions persist in stratum-balanced panels?"
        ),
        "source": {
            "database": str(database),
            "view": SOURCE_VIEW,
            "grain": "one row per school and employment graduation cohort",
            "quality_profile": source_profile,
        },
        "attribute_definitions": {
            "school_type": (
                "The exact single school_types value from the same-year 0101 school "
                "record; missing values remain 속성없음."
            ),
            "province": (
                "The exact single provinces value from the same-year 0101 school "
                "record; schools spanning more than one province are 복수시도 and "
                "missing values remain 속성없음."
            ),
        },
        "attribute_quality": {
            "missing_by_attribute": missing_by_attribute,
            "stability_by_interval": stability,
        },
        "metric_definition": (
            "sum(reported_employed_count) / sum(reported_graduate_count) within "
            "attribute, stratum, and cohort; this is not the official employment rate"
        ),
        "stratum_balanced_definition": (
            "A school OpenID must be observed in the same nonmissing stratum in every "
            "cohort of the reference-date interval."
        ),
        "findings": {
            "changes_2016_to_2020_by_attribute": headline,
            "all_vs_balanced_direction_agreement_count": direction_agreement,
            "all_vs_balanced_comparable_stratum_count": len(comparable_headline),
        },
        "validation": validation,
        "guardrails": [
            "Do not label the derived share as the official employment rate.",
            "Do not calculate a 2013-to-2014 change because the reference date changes.",
            "Do not interpret the descriptive differences as causal effects.",
            "Treat 복수시도 as a separate campus-footprint category, not as one province.",
            "Treat small strata, especially 대학원대학 and 세종, as unstable estimates.",
            "Do not use 2015-2018 further-study fields; they are outside this output and unreliable.",
        ],
        "outputs": {
            "attribute_quality_csv": {
                "path": str(quality_csv),
                "row_count": len(quality),
                "column_count": len(QUALITY_FIELDS),
                "sha256": sha256_file(quality_csv),
            },
            "attribute_stability_csv": {
                "path": str(stability_csv),
                "row_count": len(stability),
                "column_count": len(STABILITY_FIELDS),
                "sha256": sha256_file(stability_csv),
            },
            "stratified_trends_csv": {
                "path": str(trends_csv),
                "row_count": len(trends),
                "column_count": len(TREND_FIELDS),
                "sha256": sha256_file(trends_csv),
            },
        },
        "privacy": (
            "Outputs contain attribute-stratum aggregates only; OpenIDs are used in "
            "memory for panel membership and are not exported."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--quality-csv", type=Path, default=DEFAULT_QUALITY_CSV)
    parser.add_argument("--stability-csv", type=Path, default=DEFAULT_STABILITY_CSV)
    parser.add_argument("--trends-csv", type=Path, default=DEFAULT_TRENDS_CSV)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args()

    if not args.database.is_file():
        raise FileNotFoundError(args.database)
    duckdb = require_duckdb()
    connection = duckdb.connect(str(args.database), read_only=True)
    try:
        source_rows = query_source_rows(connection)
    finally:
        connection.close()

    source_profile = validate_source_rows(source_rows)
    quality = build_quality_rows(source_rows)
    stability = build_stability_rows(source_rows)
    trends = build_stratified_rows(source_rows)
    validation = validate_outputs(source_rows, trends)

    atomic_write_csv(args.quality_csv, quality, QUALITY_FIELDS)
    atomic_write_csv(args.stability_csv, stability, STABILITY_FIELDS)
    atomic_write_csv(args.trends_csv, trends, TREND_FIELDS)
    summary = build_summary(
        source_profile,
        quality,
        stability,
        trends,
        validation,
        args.database,
        args.quality_csv,
        args.stability_csv,
        args.trends_csv,
    )
    atomic_write_json(args.output_json, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "quality_row_count": len(quality),
                "stability_row_count": len(stability),
                "trend_row_count": len(trends),
                "output_json": str(args.output_json),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
