#!/usr/bin/env python3
"""Build a safe cohort-level EDSS employment trend table.

The script reads the restricted final school-cohort mart in DuckDB and writes
only 11 cohort-level aggregate rows.  Ratios are descriptive shares of the
reported graduate count, not the official employment rate.  Year-over-year
changes are calculated only within a common observation-date interval.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path("data/processed/edss/restricted/edss_all.duckdb")
DEFAULT_OUTPUT_CSV = Path("data/metadata/edss_employment_cohort_trends.csv")
DEFAULT_OUTPUT_JSON = Path("data/metadata/edss_employment_cohort_trends.json")
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

OUTPUT_FIELDS = (
    "employment_cohort_year",
    "employment_source_panel_year",
    "employment_reference_date",
    "employment_reference_date_basis",
    "employment_comparability_regime",
    "comparison_interval",
    "trend_use_status",
    "previous_comparable_cohort_year",
    "reference_date_comparable_to_previous",
    "school_count",
    "source_record_count",
    "reported_graduate_count",
    "reported_graduate_count_change_from_previous_comparable_cohort",
    "reported_employed_count",
    "reported_employed_share_of_graduates",
    "reported_employed_count_change_from_previous_comparable_cohort",
    "reported_employed_share_change_pp_from_previous_comparable_cohort",
    "reported_health_insurance_employed_count",
    "reported_health_insurance_employed_share_of_graduates",
    "reported_school_employed_count",
    "reported_further_study_count",
    "reported_further_study_share_of_graduates",
    "further_study_quality_status",
    "reported_excluded_count",
    "reported_excluded_share_of_graduates",
    "reported_other_count",
    "reported_unknown_count",
    "reported_other_unknown_share_of_graduates",
)

AGGREGATE_FIELDS = (
    "employment_cohort_year",
    "employment_source_panel_year",
    "employment_reference_date",
    "employment_reference_date_basis",
    "employment_comparability_regime",
    "school_count",
    "source_record_count",
    "reported_graduate_count",
    "reported_employed_count",
    "reported_health_insurance_employed_count",
    "reported_school_employed_count",
    "reported_further_study_count",
    "further_study_quality_status",
    "reported_excluded_count",
    "reported_other_count",
    "reported_unknown_count",
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


def descriptive_share(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        raise RuntimeError("reported graduate denominator must be positive")
    return round(numerator / denominator, 9)


def query_aggregates(connection) -> list[dict[str, object]]:
    rows = connection.execute(
        f"""
        SELECT
            employment_cohort_year,
            employment_source_panel_year,
            employment_reference_date,
            employment_reference_date_basis,
            employment_comparability_regime,
            count(*)::BIGINT AS school_count,
            sum(source_record_count)::BIGINT AS source_record_count,
            sum(reported_graduate_count)::BIGINT AS reported_graduate_count,
            sum(reported_employed_count)::BIGINT AS reported_employed_count,
            sum(reported_health_insurance_employed_count)::BIGINT
                AS reported_health_insurance_employed_count,
            sum(reported_school_employed_count)::BIGINT
                AS reported_school_employed_count,
            sum(reported_further_study_count)::BIGINT
                AS reported_further_study_count,
            min(further_study_quality_status) AS further_study_quality_status_min,
            max(further_study_quality_status) AS further_study_quality_status_max,
            sum(reported_excluded_count)::BIGINT AS reported_excluded_count,
            sum(reported_other_count)::BIGINT AS reported_other_count,
            sum(reported_unknown_count)::BIGINT AS reported_unknown_count
        FROM {SOURCE_VIEW}
        GROUP BY
            employment_cohort_year,
            employment_source_panel_year,
            employment_reference_date,
            employment_reference_date_basis,
            employment_comparability_regime
        ORDER BY employment_cohort_year
        """
    ).fetchall()

    records: list[dict[str, object]] = []
    for row in rows:
        if row[12] != row[13]:
            raise RuntimeError(
                "further-study quality status varies within cohort "
                f"{row[0]}: {row[12]!r} != {row[13]!r}"
            )
        record = dict(zip(AGGREGATE_FIELDS[:12], row[:12]))
        record["further_study_quality_status"] = row[12]
        record.update(dict(zip(AGGREGATE_FIELDS[13:], row[14:])))
        records.append(record)
    return records


def validate_aggregates(records: list[dict[str, object]]) -> None:
    cohorts = tuple(str(row["employment_cohort_year"]) for row in records)
    if cohorts != EXPECTED_COHORTS:
        raise RuntimeError(
            f"unexpected cohort sequence: expected {EXPECTED_COHORTS}, got {cohorts}"
        )

    for row in records:
        cohort = str(row["employment_cohort_year"])
        basis = str(row["employment_reference_date_basis"])
        if basis not in INTERVALS or cohort not in INTERVALS[basis]["cohorts"]:
            raise RuntimeError(
                f"cohort {cohort} has unexpected reference-date basis {basis!r}"
            )
        if int(row["school_count"]) <= 0:
            raise RuntimeError(f"cohort {cohort} has no school rows")
        if int(row["source_record_count"]) <= 0:
            raise RuntimeError(f"cohort {cohort} has no source records")
        if int(row["reported_graduate_count"]) <= 0:
            raise RuntimeError(f"cohort {cohort} has no reported graduates")
        for field in (
            "reported_employed_count",
            "reported_health_insurance_employed_count",
            "reported_school_employed_count",
            "reported_further_study_count",
            "reported_excluded_count",
            "reported_other_count",
            "reported_unknown_count",
        ):
            if int(row[field]) < 0:
                raise RuntimeError(f"cohort {cohort} has negative {field}")


def build_trend_rows(records: list[dict[str, object]]) -> list[dict[str, object]]:
    validate_aggregates(records)
    previous_by_interval: dict[str, dict[str, object]] = {}
    output: list[dict[str, object]] = []

    for aggregate in records:
        row = dict(aggregate)
        cohort = str(row["employment_cohort_year"])
        basis = str(row["employment_reference_date_basis"])
        interval = str(INTERVALS[basis]["name"])
        graduates = int(row["reported_graduate_count"])
        employed = int(row["reported_employed_count"])
        employed_share = descriptive_share(employed, graduates)
        previous = previous_by_interval.get(interval)

        row["comparison_interval"] = interval
        row["reference_date_comparable_to_previous"] = previous is not None
        row["previous_comparable_cohort_year"] = (
            str(previous["employment_cohort_year"]) if previous else None
        )
        row["trend_use_status"] = (
            "comparable_with_previous_within_reference_date_interval"
            if previous
            else "interval_start_no_yoy"
        )
        row["reported_graduate_count_change_from_previous_comparable_cohort"] = (
            graduates - int(previous["reported_graduate_count"])
            if previous
            else None
        )
        row["reported_employed_share_of_graduates"] = employed_share
        row["reported_employed_count_change_from_previous_comparable_cohort"] = (
            employed - int(previous["reported_employed_count"])
            if previous
            else None
        )
        row["reported_employed_share_change_pp_from_previous_comparable_cohort"] = (
            round(
                (
                    employed_share
                    - float(previous["reported_employed_share_of_graduates"])
                )
                * 100,
                6,
            )
            if previous
            else None
        )
        row["reported_health_insurance_employed_share_of_graduates"] = (
            descriptive_share(
                int(row["reported_health_insurance_employed_count"]), graduates
            )
        )
        row["reported_further_study_share_of_graduates"] = (
            descriptive_share(int(row["reported_further_study_count"]), graduates)
            if row["further_study_quality_status"] == "as_reported"
            else None
        )
        row["reported_excluded_share_of_graduates"] = descriptive_share(
            int(row["reported_excluded_count"]), graduates
        )
        row["reported_other_unknown_share_of_graduates"] = descriptive_share(
            int(row["reported_other_count"]) + int(row["reported_unknown_count"]),
            graduates,
        )

        ordered = {field: row.get(field) for field in OUTPUT_FIELDS}
        output.append(ordered)
        previous_by_interval[interval] = ordered

    return output


def csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    return value


def atomic_write_csv(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({key: csv_value(value) for key, value in record.items()})
    temporary.replace(path)


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_summary(
    records: list[dict[str, object]],
    output_csv: Path,
) -> dict[str, object]:
    by_cohort = {str(row["employment_cohort_year"]): row for row in records}
    return {
        "version": 1,
        "generated_at": utc_now(),
        "status": "complete_with_comparability_caveat",
        "source": {
            "database": str(DEFAULT_DATABASE),
            "view": SOURCE_VIEW,
            "grain": "one row per employment graduation cohort",
        },
        "coverage": {
            "cohort_count": len(records),
            "first_cohort_year": records[0]["employment_cohort_year"],
            "last_cohort_year": records[-1]["employment_cohort_year"],
            "school_cohort_key_count": sum(int(row["school_count"]) for row in records),
            "selected_source_record_count": sum(
                int(row["source_record_count"]) for row in records
            ),
        },
        "comparison_intervals": [
            {
                "name": definition["name"],
                "reference_date_basis": basis,
                "first_cohort_year": definition["cohorts"][0],
                "last_cohort_year": definition["cohorts"][-1],
                "cohort_count": len(definition["cohorts"]),
            }
            for basis, definition in INTERVALS.items()
        ],
        "metric_definitions": {
            "reported_employed_share_of_graduates": (
                "sum(reported_employed_count) / sum(reported_graduate_count); "
                "descriptive source share, not the official employment rate"
            ),
            "reported_employed_share_change_pp_from_previous_comparable_cohort": (
                "percentage-point change from the prior cohort only within the "
                "same reference-date interval"
            ),
            "reported_further_study_share_of_graduates": (
                "sum(reported_further_study_count) / sum(reported_graduate_count); "
                "null when the source field is all zero and not trustworthy"
            ),
        },
        "guardrails": [
            "Do not label any derived share as the official employment rate.",
            "Do not calculate a 2013-to-2014 year-over-year change because the reference date changes from June 1 to December 31.",
            "Do not use the further-study share for cohorts 2015-2018; the source field is entirely zero.",
            "Treat all results as descriptive aggregates; they do not identify causal effects.",
        ],
        "validation": {
            "expected_cohorts_present": tuple(by_cohort) == EXPECTED_COHORTS,
            "interval_start_yoy_is_null": all(
                by_cohort[cohort][
                    "reported_employed_share_change_pp_from_previous_comparable_cohort"
                ]
                is None
                for cohort in ("2010", "2014")
            ),
            "unreliable_further_study_shares_are_null": all(
                by_cohort[cohort]["reported_further_study_share_of_graduates"] is None
                for cohort in ("2015", "2016", "2017", "2018")
            ),
        },
        "output": {
            "csv": str(output_csv),
            "row_count": len(records),
            "column_count": len(OUTPUT_FIELDS),
            "sha256": sha256_file(output_csv),
        },
        "privacy": (
            "The output contains cohort-level totals only and no person-level, "
            "company, thesis, or direct identifier fields."
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
        records = build_trend_rows(query_aggregates(connection))
    finally:
        connection.close()

    atomic_write_csv(args.output_csv, records)
    summary = build_summary(records, args.output_csv)
    summary["source"]["database"] = str(args.database)
    atomic_write_json(args.output_json, summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "cohort_count": len(records),
                "output_csv": str(args.output_csv),
                "output_json": str(args.output_json),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
