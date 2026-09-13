#!/usr/bin/env python3
"""Compare all available schools with interval-balanced employment panels.

A school belongs to the balanced panel only when its OpenID is observed in
every cohort of the same reference-date interval: 2010-2013 for June 1 and
2014-2020 for December 31. Outputs contain cohort-level aggregates only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path("data/processed/edss/restricted/edss_all.duckdb")
DEFAULT_OUTPUT_CSV = Path(
    "data/metadata/edss_employment_balanced_panel_sensitivity.csv"
)
DEFAULT_OUTPUT_JSON = Path(
    "data/metadata/edss_employment_balanced_panel_sensitivity.json"
)
SOURCE_VIEW = "analysis.employment_cohort_school_2010_2020"
EXPECTED_COHORTS = tuple(str(year) for year in range(2010, 2021))
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

SENSITIVITY_SQL = f"""
WITH base AS (
    SELECT
        *,
        CASE employment_reference_date_basis
            WHEN 'june_1' THEN 'june_1_2010_2013'
            WHEN 'december_31' THEN 'december_31_2014_2020'
        END AS comparison_interval
    FROM {SOURCE_VIEW}
),
balanced_ids AS (
    SELECT comparison_interval, 개방ID
    FROM base
    GROUP BY comparison_interval, 개방ID
    HAVING count(DISTINCT employment_cohort_year) =
        CASE comparison_interval
            WHEN 'june_1_2010_2013' THEN 4
            WHEN 'december_31_2014_2020' THEN 7
        END
)
SELECT
    base.employment_cohort_year,
    base.employment_source_panel_year,
    base.employment_reference_date,
    base.employment_reference_date_basis,
    base.comparison_interval,
    count(*)::BIGINT AS all_available_school_count,
    count(*) FILTER (WHERE balanced_ids.개방ID IS NOT NULL)::BIGINT
        AS balanced_school_count,
    sum(base.source_record_count)::BIGINT
        AS all_available_source_record_count,
    sum(base.source_record_count) FILTER (
        WHERE balanced_ids.개방ID IS NOT NULL
    )::BIGINT AS balanced_source_record_count,
    sum(base.reported_graduate_count)::BIGINT
        AS all_available_reported_graduate_count,
    sum(base.reported_graduate_count) FILTER (
        WHERE balanced_ids.개방ID IS NOT NULL
    )::BIGINT AS balanced_reported_graduate_count,
    sum(base.reported_employed_count)::BIGINT
        AS all_available_reported_employed_count,
    sum(base.reported_employed_count) FILTER (
        WHERE balanced_ids.개방ID IS NOT NULL
    )::BIGINT AS balanced_reported_employed_count
FROM base
LEFT JOIN balanced_ids
    USING (comparison_interval, 개방ID)
GROUP BY
    base.employment_cohort_year,
    base.employment_source_panel_year,
    base.employment_reference_date,
    base.employment_reference_date_basis,
    base.comparison_interval
ORDER BY base.employment_cohort_year
""".strip()

AGGREGATE_FIELDS = (
    "employment_cohort_year",
    "employment_source_panel_year",
    "employment_reference_date",
    "employment_reference_date_basis",
    "comparison_interval",
    "all_available_school_count",
    "balanced_school_count",
    "all_available_source_record_count",
    "balanced_source_record_count",
    "all_available_reported_graduate_count",
    "balanced_reported_graduate_count",
    "all_available_reported_employed_count",
    "balanced_reported_employed_count",
)

OUTPUT_FIELDS = (
    "employment_cohort_year",
    "employment_source_panel_year",
    "employment_reference_date",
    "employment_reference_date_basis",
    "comparison_interval",
    "previous_comparable_cohort_year",
    "all_available_school_count",
    "balanced_school_count",
    "balanced_school_coverage_share",
    "schools_added_from_previous_comparable_cohort",
    "schools_missing_from_previous_comparable_cohort",
    "all_available_source_record_count",
    "balanced_source_record_count",
    "balanced_source_record_coverage_share",
    "all_available_reported_graduate_count",
    "balanced_reported_graduate_count",
    "balanced_graduate_coverage_share",
    "all_available_reported_employed_count",
    "balanced_reported_employed_count",
    "all_available_reported_employed_share_of_graduates",
    "balanced_reported_employed_share_of_graduates",
    "balanced_minus_all_reported_employed_share_pp",
    "all_available_share_change_pp_from_previous_comparable_cohort",
    "balanced_share_change_pp_from_previous_comparable_cohort",
    "balanced_minus_all_yoy_change_pp",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_duckdb():
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "duckdb is required; run with "
            "`uv run --with duckdb==1.4.1 python ...`"
        ) from exc
    return duckdb


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise RuntimeError("ratio denominator must be positive")
    return round(numerator / denominator, 9)


def query_aggregates(connection) -> list[dict[str, object]]:
    return [
        dict(zip(AGGREGATE_FIELDS, row))
        for row in connection.execute(SENSITIVITY_SQL).fetchall()
    ]


def query_source_quality(connection) -> dict[str, object]:
    row = connection.execute(
        f"""
        WITH duplicate_keys AS (
            SELECT employment_cohort_year, 개방ID
            FROM {SOURCE_VIEW}
            GROUP BY employment_cohort_year, 개방ID
            HAVING count(*) > 1
        )
        SELECT
            count(*)::BIGINT,
            count(*) FILTER (
                WHERE trim(coalesce(개방ID, '')) = ''
            )::BIGINT,
            (SELECT count(*)::BIGINT FROM duplicate_keys),
            count(DISTINCT employment_cohort_year)::BIGINT,
            min(employment_cohort_year),
            max(employment_cohort_year)
        FROM {SOURCE_VIEW}
        """
    ).fetchone()
    return {
        "row_count": int(row[0]),
        "blank_open_id_row_count": int(row[1]),
        "duplicate_school_cohort_key_count": int(row[2]),
        "cohort_count": int(row[3]),
        "first_cohort_year": str(row[4]),
        "last_cohort_year": str(row[5]),
    }


def validate_source_quality(quality: dict[str, object]) -> None:
    expected = {
        "row_count": 5969,
        "blank_open_id_row_count": 0,
        "duplicate_school_cohort_key_count": 0,
        "cohort_count": 11,
        "first_cohort_year": "2010",
        "last_cohort_year": "2020",
    }
    if quality != expected:
        raise RuntimeError(
            f"unexpected source mart quality profile: expected {expected}, got {quality}"
        )


def query_composition_counts(connection) -> dict[str, dict[str, int | None]]:
    rows = connection.execute(
        f"""
        SELECT employment_cohort_year, employment_reference_date_basis, 개방ID
        FROM {SOURCE_VIEW}
        ORDER BY employment_cohort_year, 개방ID
        """
    ).fetchall()
    ids_by_cohort: dict[str, set[str]] = {}
    basis_by_cohort: dict[str, str] = {}
    for cohort, basis, open_id in rows:
        ids_by_cohort.setdefault(str(cohort), set()).add(str(open_id))
        basis_by_cohort[str(cohort)] = str(basis)

    output: dict[str, dict[str, int | None]] = {}
    previous_by_interval: dict[str, set[str]] = {}
    for cohort in EXPECTED_COHORTS:
        basis = basis_by_cohort[cohort]
        interval = str(INTERVALS[basis]["name"])
        current = ids_by_cohort[cohort]
        previous = previous_by_interval.get(interval)
        output[cohort] = {
            "schools_added_from_previous_comparable_cohort": (
                len(current - previous) if previous is not None else None
            ),
            "schools_missing_from_previous_comparable_cohort": (
                len(previous - current) if previous is not None else None
            ),
        }
        previous_by_interval[interval] = current
    return output


def validate_aggregates(records: list[dict[str, object]]) -> None:
    cohorts = tuple(str(row["employment_cohort_year"]) for row in records)
    if cohorts != EXPECTED_COHORTS:
        raise RuntimeError(
            f"unexpected cohort sequence: expected {EXPECTED_COHORTS}, got {cohorts}"
        )

    balanced_counts_by_interval: dict[str, set[int]] = {}
    for row in records:
        cohort = str(row["employment_cohort_year"])
        basis = str(row["employment_reference_date_basis"])
        interval = str(row["comparison_interval"])
        expected = INTERVALS.get(basis)
        if expected is None or cohort not in expected["cohorts"]:
            raise RuntimeError(
                f"cohort {cohort} has unexpected reference-date basis {basis!r}"
            )
        if interval != expected["name"]:
            raise RuntimeError(
                f"cohort {cohort} has unexpected comparison interval {interval!r}"
            )
        balanced_counts_by_interval.setdefault(interval, set()).add(
            int(row["balanced_school_count"])
        )

        all_schools = int(row["all_available_school_count"])
        balanced_schools = int(row["balanced_school_count"])
        if not 0 < balanced_schools <= all_schools:
            raise RuntimeError(f"cohort {cohort} has invalid balanced school count")
        for all_field, balanced_field in (
            ("all_available_source_record_count", "balanced_source_record_count"),
            (
                "all_available_reported_graduate_count",
                "balanced_reported_graduate_count",
            ),
            (
                "all_available_reported_employed_count",
                "balanced_reported_employed_count",
            ),
        ):
            all_value = int(row[all_field])
            balanced_value = int(row[balanced_field])
            if not 0 <= balanced_value <= all_value:
                raise RuntimeError(
                    f"cohort {cohort} has invalid subset totals for {balanced_field}"
                )
        if int(row["all_available_reported_graduate_count"]) <= 0:
            raise RuntimeError(f"cohort {cohort} has no reported graduates")
        if int(row["balanced_reported_graduate_count"]) <= 0:
            raise RuntimeError(f"cohort {cohort} has no balanced reported graduates")

    for interval, counts in balanced_counts_by_interval.items():
        if len(counts) != 1:
            raise RuntimeError(
                f"balanced school count varies within interval {interval}: {counts}"
            )


def build_sensitivity_rows(
    records: list[dict[str, object]],
    composition: dict[str, dict[str, int | None]],
) -> list[dict[str, object]]:
    validate_aggregates(records)
    if tuple(composition) != EXPECTED_COHORTS:
        raise RuntimeError("composition counts do not cover the expected cohorts")

    previous_by_interval: dict[str, dict[str, object]] = {}
    output: list[dict[str, object]] = []
    for aggregate in records:
        row = dict(aggregate)
        cohort = str(row["employment_cohort_year"])
        interval = str(row["comparison_interval"])
        previous = previous_by_interval.get(interval)

        all_graduates = int(row["all_available_reported_graduate_count"])
        balanced_graduates = int(row["balanced_reported_graduate_count"])
        all_share = ratio(
            int(row["all_available_reported_employed_count"]), all_graduates
        )
        balanced_share = ratio(
            int(row["balanced_reported_employed_count"]), balanced_graduates
        )

        row["previous_comparable_cohort_year"] = (
            str(previous["employment_cohort_year"]) if previous else None
        )
        row["balanced_school_coverage_share"] = ratio(
            int(row["balanced_school_count"]),
            int(row["all_available_school_count"]),
        )
        row.update(composition[cohort])
        row["balanced_source_record_coverage_share"] = ratio(
            int(row["balanced_source_record_count"]),
            int(row["all_available_source_record_count"]),
        )
        row["balanced_graduate_coverage_share"] = ratio(
            balanced_graduates, all_graduates
        )
        row["all_available_reported_employed_share_of_graduates"] = all_share
        row["balanced_reported_employed_share_of_graduates"] = balanced_share
        row["balanced_minus_all_reported_employed_share_pp"] = round(
            (balanced_share - all_share) * 100, 6
        )
        row["all_available_share_change_pp_from_previous_comparable_cohort"] = (
            round(
                (
                    all_share
                    - float(
                        previous[
                            "all_available_reported_employed_share_of_graduates"
                        ]
                    )
                )
                * 100,
                6,
            )
            if previous
            else None
        )
        row["balanced_share_change_pp_from_previous_comparable_cohort"] = (
            round(
                (
                    balanced_share
                    - float(
                        previous["balanced_reported_employed_share_of_graduates"]
                    )
                )
                * 100,
                6,
            )
            if previous
            else None
        )
        row["balanced_minus_all_yoy_change_pp"] = (
            round(
                float(
                    row[
                        "balanced_share_change_pp_from_previous_comparable_cohort"
                    ]
                )
                - float(
                    row[
                        "all_available_share_change_pp_from_previous_comparable_cohort"
                    ]
                ),
                6,
            )
            if previous
            else None
        )

        ordered = {field: row.get(field) for field in OUTPUT_FIELDS}
        output.append(ordered)
        previous_by_interval[interval] = ordered
    return output


def csv_value(value: object) -> object:
    return "" if value is None else value


def atomic_write_csv(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in records:
            writer.writerow({key: csv_value(value) for key, value in row.items()})
    temporary.replace(path)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def change_between(
    by_cohort: dict[str, dict[str, object]],
    start: str,
    end: str,
    field: str,
) -> float:
    return round(
        (float(by_cohort[end][field]) - float(by_cohort[start][field])) * 100,
        6,
    )


def build_summary(
    records: list[dict[str, object]],
    output_csv: Path,
    database: Path,
    source_quality: dict[str, object],
) -> dict[str, object]:
    by_cohort = {str(row["employment_cohort_year"]): row for row in records}
    comparable = [row for row in records if row["previous_comparable_cohort_year"]]
    direction_agreements = sum(
        (
            float(
                row[
                    "all_available_share_change_pp_from_previous_comparable_cohort"
                ]
            )
            > 0
        )
        == (
            float(
                row["balanced_share_change_pp_from_previous_comparable_cohort"]
            )
            > 0
        )
        for row in comparable
    )
    maximum_gap = max(
        records,
        key=lambda row: abs(
            float(row["balanced_minus_all_reported_employed_share_pp"])
        ),
    )
    all_2016_2020 = change_between(
        by_cohort,
        "2016",
        "2020",
        "all_available_reported_employed_share_of_graduates",
    )
    balanced_2016_2020 = change_between(
        by_cohort,
        "2016",
        "2020",
        "balanced_reported_employed_share_of_graduates",
    )

    return {
        "version": 1,
        "generated_at": utc_now(),
        "status": "share_with_caveats",
        "question": (
            "Does the reported-employed descriptive share trend persist when "
            "school composition is held fixed within each reference-date interval?"
        ),
        "source": {
            "database": str(database),
            "view": SOURCE_VIEW,
            "grain": "one aggregate row per employment graduation cohort",
        },
        "balanced_panel_definition": (
            "A school OpenID must be observed in every cohort of its reference-date "
            "interval: 2010-2013 for June 1 or 2014-2020 for December 31."
        ),
        "coverage": {
            "cohort_count": len(records),
            "balanced_school_count_by_interval": {
                "june_1_2010_2013": int(by_cohort["2010"]["balanced_school_count"]),
                "december_31_2014_2020": int(
                    by_cohort["2014"]["balanced_school_count"]
                ),
            },
            "minimum_balanced_school_coverage_share": min(
                float(row["balanced_school_coverage_share"]) for row in records
            ),
            "minimum_balanced_graduate_coverage_share": min(
                float(row["balanced_graduate_coverage_share"]) for row in records
            ),
        },
        "findings": {
            "all_available_2016_to_2020_change_pp": all_2016_2020,
            "balanced_2016_to_2020_change_pp": balanced_2016_2020,
            "sensitivity_difference_pp": round(
                balanced_2016_2020 - all_2016_2020, 6
            ),
            "direction_agreement_count": direction_agreements,
            "comparable_transition_count": len(comparable),
            "maximum_absolute_balanced_minus_all_gap_pp": abs(
                float(maximum_gap["balanced_minus_all_reported_employed_share_pp"])
            ),
            "maximum_gap_cohort_year": maximum_gap["employment_cohort_year"],
        },
        "validation": {
            "source_mart_quality": source_quality,
            "expected_cohorts_present": tuple(by_cohort) == EXPECTED_COHORTS,
            "interval_start_changes_are_null": all(
                by_cohort[year][
                    "balanced_share_change_pp_from_previous_comparable_cohort"
                ]
                is None
                for year in ("2010", "2014")
            ),
            "balanced_school_count_constant_within_interval": True,
            "all_comparable_transition_directions_agree": (
                direction_agreements == len(comparable)
            ),
        },
        "caveats": [
            "The derived share is not the official employment rate.",
            "The 2013-to-2014 boundary is not compared because the reference date changes.",
            "Balancing controls observed school composition but introduces survivorship selection and does not stabilize changing metric definitions.",
            "The result is descriptive and does not identify a causal effect.",
        ],
        "output": {
            "csv": str(output_csv),
            "row_count": len(records),
            "column_count": len(OUTPUT_FIELDS),
            "sha256": sha256_file(output_csv),
        },
        "privacy": (
            "Only cohort-level totals are written; school OpenIDs are used in-memory "
            "for membership checks and are not exported."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args()

    if not args.database.is_file():
        raise FileNotFoundError(args.database)
    duckdb = require_duckdb()
    connection = duckdb.connect(str(args.database), read_only=True)
    try:
        source_quality = query_source_quality(connection)
        validate_source_quality(source_quality)
        aggregates = query_aggregates(connection)
        composition = query_composition_counts(connection)
    finally:
        connection.close()

    records = build_sensitivity_rows(aggregates, composition)
    atomic_write_csv(args.output_csv, records)
    summary = build_summary(
        records, args.output_csv, args.database, source_quality
    )
    atomic_write_json(args.output_json, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "cohort_count": len(records),
                "balanced_school_count_by_interval": summary["coverage"][
                    "balanced_school_count_by_interval"
                ],
                "output_csv": str(args.output_csv),
                "output_json": str(args.output_json),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
