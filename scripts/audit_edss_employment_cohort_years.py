#!/usr/bin/env python3
"""Audit EDSS employment source years against observed graduation cohorts.

The audit reads the restricted DuckDB in read-only mode. It emits only
year-level counts and school-level comparison counts; no person-level values
or company fields are written to the outputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DATABASE = Path("data/processed/edss/restricted/edss_all.duckdb")
DEFAULT_OUTPUT_CSV = Path(
    "data/metadata/edss_employment_cohort_year_audit.csv"
)
DEFAULT_OUTPUT_JSON = Path(
    "data/metadata/edss_employment_cohort_year_audit.json"
)
SOURCE_VIEW = "analysis.employment_legacy_2010_2022"

METRICS = (
    ("졸업학생수", "reported_graduate_count"),
    ("취업자수", "reported_employed_count"),
    ("건강보험취업자수", "reported_health_insurance_employed_count"),
    ("교내취업자수", "reported_school_employed_count"),
    ("진학자수", "reported_further_study_count"),
    ("입대자수", "reported_military_service_count"),
    ("취업불가능자수", "reported_employment_unavailable_count"),
    ("외국인유학생수", "reported_foreign_student_count"),
    ("제외인정자수", "reported_excluded_count"),
    ("기타_총계", "reported_other_count"),
    ("미상", "reported_unknown_count"),
)

OFFICIAL_SAME_LABEL_DEFINITIONS = {
    "2021": {
        "target_months": ("202008", "202102"),
        "reference_date": "2021-12-31",
        "source_url": (
            "https://kess.kedi.re.kr/publ/fileDownload.do?"
            "fileSeq=1064949&publItemId=95638"
        ),
    },
    "2022": {
        "target_months": ("202108", "202202"),
        "reference_date": "2022-12-31",
        "source_url": (
            "https://kess.kedi.re.kr/publ/fileDownload.do?"
            "fileSeq=1075201&publItemId=102507"
        ),
    },
}

METHODOLOGY_SOURCE = {
    "finding": (
        "2012년부터 6월 1일과 12월 31일 기준 연 2회 조사, "
        "2015년부터 12월 31일 기준 조사로 일원화"
    ),
    "source_url": (
        "https://kess.kedi.re.kr/publ/fileDownload.do?"
        "fileSeq=1012636&publItemId=69896"
    ),
}

OUTPUT_FIELDS = (
    "source_year",
    "source_row_count",
    "distinct_open_id_count",
    "source_survey_year_value_count",
    "source_survey_year_mismatch_row_count",
    "graduation_month_min",
    "graduation_month_max",
    "graduation_month_distinct_count",
    "graduation_month_blank_count",
    "graduation_month_invalid_count",
    "inferred_cohort_year",
    "inferred_cohort_row_count",
    "inferred_cohort_share",
    "primary_august_february_row_count",
    "primary_august_february_share",
    "source_minus_cohort_years",
    "time_axis_status",
    "cohort_source_year_count",
    "cohort_source_years",
    "same_cohort_previous_source_year",
    "same_cohort_month_distribution_equal_to_previous",
    "same_cohort_shared_open_id_count",
    "same_cohort_exact_school_aggregate_match_count",
    "same_cohort_exact_school_aggregate_match_share",
    "official_same_label_target_months",
    "official_same_label_target_row_count",
    "official_same_label_status",
    "cohort_use_status",
    "cohort_analysis_eligible",
    "severity",
    "recommended_action",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def require_duckdb():
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "duckdb is required; run with "
            "`uv run --with duckdb==1.4.1 python ...`"
        ) from exc
    return duckdb


def valid_graduation_month(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{4}(0[1-9]|1[0-2])", value))


def row_cohort_year(graduation_month: str) -> int:
    """Map July-December graduations to the following February cohort year."""
    if not valid_graduation_month(graduation_month):
        raise ValueError(f"invalid graduation month: {graduation_month!r}")
    year = int(graduation_month[:4])
    month = int(graduation_month[4:])
    return year + (1 if month >= 7 else 0)


def relation_columns(connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'analysis'
              AND table_name = 'employment_legacy_2010_2022'
            """
        ).fetchall()
    }


def source_year_stats(connection) -> dict[str, dict]:
    rows = connection.execute(
        f"""
        SELECT
            _panel_year,
            count(*)::BIGINT,
            count(DISTINCT 개방ID)::BIGINT,
            count(DISTINCT 조사년도)::BIGINT,
            count(*) FILTER (
                WHERE trim(coalesce(조사년도, '')) <> _panel_year
            )::BIGINT,
            count(*) FILTER (
                WHERE trim(coalesce(개방ID, '')) = ''
            )::BIGINT
        FROM {SOURCE_VIEW}
        GROUP BY _panel_year
        ORDER BY _panel_year
        """
    ).fetchall()
    return {
        row[0]: {
            "source_row_count": int(row[1]),
            "distinct_open_id_count": int(row[2]),
            "source_survey_year_value_count": int(row[3]),
            "source_survey_year_mismatch_row_count": int(row[4]),
            "blank_open_id_row_count": int(row[5]),
        }
        for row in rows
    }


def graduation_month_counts(connection) -> dict[str, Counter[str]]:
    rows = connection.execute(
        f"""
        SELECT _panel_year, trim(coalesce(졸업년월, '')), count(*)::BIGINT
        FROM {SOURCE_VIEW}
        GROUP BY _panel_year, trim(coalesce(졸업년월, ''))
        ORDER BY _panel_year, trim(coalesce(졸업년월, ''))
        """
    ).fetchall()
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for source_year, graduation_month, row_count in rows:
        counts[source_year][graduation_month] = int(row_count)
    return counts


def school_aggregates(connection) -> dict[str, dict[str, tuple[int, ...]]]:
    expressions = ["count(*)::BIGINT AS source_record_count"]
    for source_field, output_field in METRICS:
        source = quote_identifier(source_field)
        output = quote_identifier(output_field)
        expressions.append(
            "sum(try_cast(replace(trim(cast("
            f"{source} AS VARCHAR)), ',', '') AS BIGINT))::BIGINT AS {output}"
        )
    rows = connection.execute(
        f"""
        SELECT _panel_year, 개방ID, {', '.join(expressions)}
        FROM {SOURCE_VIEW}
        GROUP BY _panel_year, 개방ID
        ORDER BY _panel_year, 개방ID
        """
    ).fetchall()
    aggregates: dict[str, dict[str, tuple[int, ...]]] = defaultdict(dict)
    for row in rows:
        aggregates[row[0]][row[1]] = tuple(
            int(value) if value is not None else -1 for value in row[2:]
        )
    return aggregates


def build_month_profile(
    source_year: str,
    row_count: int,
    counts: Counter[str],
) -> dict:
    blank_count = counts.get("", 0)
    valid_counts = {
        month: count
        for month, count in counts.items()
        if valid_graduation_month(month)
    }
    invalid_count = sum(counts.values()) - blank_count - sum(valid_counts.values())
    cohort_counts: Counter[int] = Counter()
    for month, count in valid_counts.items():
        cohort_counts[row_cohort_year(month)] += count
    if not cohort_counts:
        return {
            "graduation_month_min": "",
            "graduation_month_max": "",
            "graduation_month_distinct_count": 0,
            "graduation_month_blank_count": blank_count,
            "graduation_month_invalid_count": invalid_count,
            "inferred_cohort_year": "",
            "inferred_cohort_row_count": 0,
            "inferred_cohort_share": 0.0,
            "primary_august_february_row_count": 0,
            "primary_august_february_share": 0.0,
            "source_minus_cohort_years": "",
            "cohort_mode_tie": False,
        }

    max_count = max(cohort_counts.values())
    modal_cohorts = sorted(
        cohort for cohort, count in cohort_counts.items() if count == max_count
    )
    inferred_cohort = modal_cohorts[-1]
    primary_months = (f"{inferred_cohort - 1}08", f"{inferred_cohort}02")
    primary_count = sum(valid_counts.get(month, 0) for month in primary_months)
    valid_months = sorted(valid_counts)
    return {
        "graduation_month_min": valid_months[0],
        "graduation_month_max": valid_months[-1],
        "graduation_month_distinct_count": len(valid_months),
        "graduation_month_blank_count": blank_count,
        "graduation_month_invalid_count": invalid_count,
        "inferred_cohort_year": str(inferred_cohort),
        "inferred_cohort_row_count": max_count,
        "inferred_cohort_share": round(max_count / row_count, 9),
        "primary_august_february_row_count": primary_count,
        "primary_august_february_share": round(primary_count / row_count, 9),
        "source_minus_cohort_years": int(source_year) - inferred_cohort,
        "cohort_mode_tie": len(modal_cohorts) > 1,
    }


def compare_same_cohort_sources(
    source_years: list[str],
    month_counts: dict[str, Counter[str]],
    aggregates: dict[str, dict[str, tuple[int, ...]]],
) -> list[dict]:
    comparisons = []
    for previous, current in zip(source_years, source_years[1:]):
        previous_values = aggregates[previous]
        current_values = aggregates[current]
        shared_ids = sorted(previous_values.keys() & current_values.keys())
        exact_count = sum(
            previous_values[open_id] == current_values[open_id]
            for open_id in shared_ids
        )
        comparisons.append(
            {
                "previous_source_year": previous,
                "current_source_year": current,
                "previous_open_id_count": len(previous_values),
                "current_open_id_count": len(current_values),
                "shared_open_id_count": len(shared_ids),
                "exact_school_aggregate_match_count": exact_count,
                "exact_school_aggregate_match_share": round(
                    exact_count / len(shared_ids), 9
                )
                if shared_ids
                else 0.0,
                "graduation_month_distribution_equal": (
                    month_counts[previous] == month_counts[current]
                ),
                "complete_exact_repeat": (
                    len(previous_values)
                    == len(current_values)
                    == len(shared_ids)
                    == exact_count
                    and month_counts[previous] == month_counts[current]
                ),
            }
        )
    return comparisons


def audit_connection(connection) -> tuple[dict, list[dict]]:
    required = {
        "_panel_year",
        "조사년도",
        "개방ID",
        "졸업년월",
        *(source for source, _output in METRICS),
    }
    missing = sorted(required - relation_columns(connection))
    if missing:
        raise RuntimeError(f"{SOURCE_VIEW} is missing required columns: {missing}")

    year_stats = source_year_stats(connection)
    month_counts = graduation_month_counts(connection)
    aggregates = school_aggregates(connection)
    records = []
    for source_year, stats in sorted(year_stats.items()):
        profile = build_month_profile(
            source_year, stats["source_row_count"], month_counts[source_year]
        )
        records.append({"source_year": source_year, **stats, **profile})

    cohort_groups: dict[str, list[str]] = defaultdict(list)
    for record in records:
        if record["inferred_cohort_year"]:
            cohort_groups[record["inferred_cohort_year"]].append(
                record["source_year"]
            )
    for source_years in cohort_groups.values():
        source_years.sort()

    comparisons = []
    for cohort_year, source_years in sorted(cohort_groups.items()):
        group_comparisons = compare_same_cohort_sources(
            source_years, month_counts, aggregates
        )
        for comparison in group_comparisons:
            comparison["inferred_cohort_year"] = cohort_year
        comparisons.extend(group_comparisons)
    comparison_by_current = {
        row["current_source_year"]: row for row in comparisons
    }

    for record in records:
        source_year = record["source_year"]
        cohort_year = record["inferred_cohort_year"]
        source_years = cohort_groups.get(cohort_year, [])
        comparison = comparison_by_current.get(source_year)
        group_comparisons = [
            item
            for item in comparisons
            if item["inferred_cohort_year"] == cohort_year
        ]
        group_is_exact_repeat = bool(group_comparisons) and all(
            item["complete_exact_repeat"] for item in group_comparisons
        )

        difference = record["source_minus_cohort_years"]
        if difference == 0:
            time_axis_status = "source_year_matches_inferred_cohort"
        elif difference == 1:
            time_axis_status = "source_year_one_year_after_inferred_cohort"
        else:
            time_axis_status = "source_year_other_cohort_offset"

        if len(source_years) == 1:
            cohort_use_status = "eligible_unique_cohort"
            eligible = True
            severity = "medium" if difference else "low"
            action = (
                "rekey_to_inferred_cohort_year"
                if difference
                else "use_source_year_as_cohort_year"
            )
        elif group_is_exact_repeat:
            if source_year == source_years[0]:
                cohort_use_status = "eligible_first_of_exact_repeat"
                eligible = True
                severity = "medium"
                action = "retain_first_source_and_rekey_to_inferred_cohort_year"
            else:
                cohort_use_status = "exclude_exact_repeat"
                eligible = False
                severity = "high"
                action = "exclude_from_cohort_and_time_series_analysis"
        else:
            cohort_use_status = "review_repeated_distinct_wave"
            eligible = False
            severity = "high"
            action = "resolve_reference_date_and_methodology_before_wave_selection"

        official = OFFICIAL_SAME_LABEL_DEFINITIONS.get(source_year)
        if official:
            target_months = official["target_months"]
            target_count = sum(
                month_counts[source_year].get(month, 0) for month in target_months
            )
            official_status = (
                "conflict_no_official_target_months"
                if target_count == 0
                else "official_target_months_present"
            )
        else:
            target_months = ()
            target_count = 0
            official_status = "not_checked"

        record.update(
            {
                "time_axis_status": time_axis_status,
                "cohort_source_year_count": len(source_years),
                "cohort_source_years": "|".join(source_years),
                "same_cohort_previous_source_year": comparison[
                    "previous_source_year"
                ]
                if comparison
                else "",
                "same_cohort_month_distribution_equal_to_previous": (
                    str(comparison["graduation_month_distribution_equal"]).lower()
                    if comparison
                    else ""
                ),
                "same_cohort_shared_open_id_count": comparison[
                    "shared_open_id_count"
                ]
                if comparison
                else 0,
                "same_cohort_exact_school_aggregate_match_count": comparison[
                    "exact_school_aggregate_match_count"
                ]
                if comparison
                else 0,
                "same_cohort_exact_school_aggregate_match_share": comparison[
                    "exact_school_aggregate_match_share"
                ]
                if comparison
                else 0.0,
                "official_same_label_target_months": "|".join(target_months),
                "official_same_label_target_row_count": target_count,
                "official_same_label_status": official_status,
                "cohort_use_status": cohort_use_status,
                "cohort_analysis_eligible": str(eligible).lower(),
                "severity": severity,
                "recommended_action": action,
            }
        )

    public_records = [
        {field: record[field] for field in OUTPUT_FIELDS} for record in records
    ]
    cohort_group_summary = {
        cohort: years
        for cohort, years in sorted(cohort_groups.items())
        if len(years) > 1
    }
    status_counts = Counter(
        record["cohort_use_status"] for record in public_records
    )
    summary = {
        "audit_version": 1,
        "generated_at": utc_now(),
        "status": (
            "review_required"
            if status_counts["review_repeated_distinct_wave"]
            else "complete"
        ),
        "source_view": SOURCE_VIEW,
        "grain": "one row per source year in CSV; year-level and school-aggregate evidence only",
        "source_year_range": [records[0]["source_year"], records[-1]["source_year"]],
        "source_year_count": len(records),
        "source_row_count": sum(
            record["source_row_count"] for record in public_records
        ),
        "inferred_cohort_year_range": [
            min(cohort_groups),
            max(cohort_groups),
        ],
        "inferred_cohort_year_count": len(cohort_groups),
        "repeated_cohort_groups": cohort_group_summary,
        "cohort_use_status_counts": dict(sorted(status_counts.items())),
        "cohort_analysis_eligible_source_year_count": sum(
            record["cohort_analysis_eligible"] == "true"
            for record in public_records
        ),
        "official_same_label_conflict_years": [
            record["source_year"]
            for record in public_records
            if record["official_same_label_status"].startswith("conflict_")
        ],
        "same_cohort_comparisons": comparisons,
        "official_same_label_definitions": OFFICIAL_SAME_LABEL_DEFINITIONS,
        "methodology_context": METHODOLOGY_SOURCE,
        "safe_use_rule": (
            "Never interpret _panel_year as the graduation cohort without this "
            "audit. Filter to cohort_analysis_eligible=true and group by "
            "inferred_cohort_year. Do not select either 2014-cohort wave until "
            "its reference date and methodology are resolved."
        ),
        "privacy": (
            "Outputs contain only year-level counts and school-level comparison "
            "counts. No person identifier, company, thesis, or row-level value "
            "is emitted."
        ),
    }
    return summary, public_records


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def atomic_write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(records)
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args()

    duckdb = require_duckdb()
    connection = duckdb.connect(str(args.database), read_only=True)
    try:
        summary, records = audit_connection(connection)
    finally:
        connection.close()
    atomic_write_csv(args.output_csv, records)
    atomic_write_json(args.output_json, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
