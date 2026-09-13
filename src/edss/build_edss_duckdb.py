#!/usr/bin/env python3
"""Build a resumable DuckDB warehouse from every cataloged EDSS panel.

Each logical panel is stored as a separate table so incompatible grains and
schemas are never flattened into one relation.  The full database is restricted
because it contains the historical employment panel.  The analysis layer
enforces the 2023–2024 schema break: the legacy view ends in 2022, while the
standalone 2023–2024 view contains no canonical or candidate OpenID column.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SOURCE_SCHEMAS = {
    "고등교육통계": "higher_education",
    "대학정보공시": "university_disclosure",
    "취업통계": "employment",
}
NULL_SENTINEL = "__EDSS_NULL_SENTINEL_NEVER_PRESENT__"
PROVENANCE_COLUMNS = (
    "_source_provider",
    "_source_area",
    "_source_catalog_code",
    "_source_dataset",
    "_source_domn_code",
    "_source_archive",
    "_source_archive_sha256",
    "_source_member",
    "_source_row_number",
    "_source_row_id",
    "_source_row_hash",
    "_panel_year",
)
DEFAULT_CATALOG = Path("data/metadata/edss_panel_catalog.csv")
DEFAULT_DATABASE = Path("data/processed/edss/restricted/edss_all.duckdb")
DEFAULT_AUDIT = Path("data/metadata/edss_duckdb_build.json")
DEFAULT_STANDALONE_EMPLOYMENT = Path(
    "data/processed/edss/derived/employment_2023_2024_school_department.csv.gz"
)
DEFAULT_IDENTITY_RESOLUTION_SUMMARY = Path(
    "data/metadata/edss_remaining_identity_gap_resolution.json"
)
DEFAULT_SCHOOL_YEAR_BRIDGE = Path("data/metadata/edss_school_year_bridge.csv")
DEFAULT_BRIDGE_SUMMARY = Path("data/metadata/edss_school_year_bridge_summary.json")
DEFAULT_SCHOOL_YEAR_CORE_DICTIONARY = Path(
    "data/metadata/edss_school_year_core_data_dictionary.csv"
)
DEFAULT_EMPLOYMENT_SCHOOL_YEAR_DICTIONARY = Path(
    "data/metadata/edss_employment_school_year_data_dictionary.csv"
)
DEFAULT_EMPLOYMENT_COHORT_AUDIT = Path(
    "data/metadata/edss_employment_cohort_year_audit.csv"
)
DEFAULT_EMPLOYMENT_COHORT_DICTIONARY = Path(
    "data/metadata/edss_employment_cohort_school_data_dictionary.csv"
)
SCHOOL_YEAR_CORE_METRICS = (
    ("고등교육학교_재적학생수", "enrolled_student_count"),
    ("고등교육학교_재적여학생수", "female_enrolled_student_count"),
    ("고등교육학교_입학생수", "entrant_count"),
    ("고등교육학교_여자입학생수", "female_entrant_count"),
    ("고등교육학교_졸업생수", "graduate_count"),
    ("고등교육학교_여자졸업생수", "female_graduate_count"),
    ("고등교육학교_교원수", "faculty_count"),
    ("고등교육학교_여자교원수", "female_faculty_count"),
    ("고등교육학교_사무직원수", "staff_count"),
    ("고등교육학교_여자사무직원수", "female_staff_count"),
    ("고등교육학교_건물면적", "building_area"),
    ("고등교육학교_학과수", "department_count"),
)
EMPLOYMENT_SCHOOL_YEAR_METRICS = (
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
EMPLOYMENT_COHORT_EXPECTED_SELECTION = {
    "2010": ("2010", "june_1", "eligible_unique_cohort"),
    "2011": ("2011", "june_1", "eligible_unique_cohort"),
    "2012": ("2012", "june_1", "eligible_unique_cohort"),
    "2013": ("2013", "june_1", "eligible_unique_cohort"),
    "2015": ("2014", "december_31", "eligible_transition_december_wave"),
    "2016": ("2015", "december_31", "eligible_unique_cohort"),
    "2017": ("2016", "december_31", "eligible_unique_cohort"),
    "2018": ("2017", "december_31", "eligible_unique_cohort"),
    "2019": ("2018", "december_31", "eligible_unique_cohort"),
    "2020": ("2019", "december_31", "eligible_unique_cohort"),
    "2021": ("2020", "december_31", "eligible_first_of_exact_repeat"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def table_name(catalog_code: str) -> str:
    if not re.fullmatch(r"[0-9A-Za-z_]+", catalog_code):
        raise RuntimeError(f"unsafe catalog code: {catalog_code!r}")
    return f"panel_{catalog_code.lower()}"


def table_key(row: dict[str, str]) -> tuple[str, str]:
    source = row["source"].strip()
    if source not in SOURCE_SCHEMAS:
        raise RuntimeError(f"unknown EDSS source: {source!r}")
    return SOURCE_SCHEMAS[source], table_name(row["catalog_code"].strip())


def read_catalog(path: Path, repo_root: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "source",
            "catalog_code",
            "dataset",
            "access_tier",
            "row_count",
            "column_count",
            "output_path",
            "output_bytes",
            "output_sha256",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise RuntimeError(f"catalog missing required fields: {missing}")
        rows = list(reader)

    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = table_key(row)
        if key in seen:
            raise RuntimeError(f"duplicate DuckDB table key: {key}")
        seen.add(key)

    for row in rows:
        source_path = repo_root / row["output_path"]
        if not source_path.is_file():
            raise RuntimeError(f"cataloged panel is missing: {source_path}")
        if source_path.stat().st_size != int(row["output_bytes"]):
            raise RuntimeError(f"cataloged panel size mismatch: {source_path}")
        if int(row["row_count"]) < 0 or int(row["column_count"]) <= 0:
            raise RuntimeError(f"invalid catalog dimensions: {row}")
        with gzip.open(source_path, "rt", encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
        expected_loaded_columns = int(row["column_count"]) + len(PROVENANCE_COLUMNS)
        if tuple(header[: len(PROVENANCE_COLUMNS)]) != PROVENANCE_COLUMNS:
            raise RuntimeError(f"unexpected provenance columns: {source_path}")
        if len(header) != expected_loaded_columns:
            raise RuntimeError(
                f"catalog/header column mismatch for {source_path}: "
                f"expected {expected_loaded_columns}, got {len(header)}"
            )
        if len(set(header)) != len(header):
            raise RuntimeError(f"duplicate CSV header columns: {source_path}")
    return rows


def require_duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required; run with `uv run --with duckdb==1.4.1 python ...`"
        ) from exc
    return duckdb


def relation_exists(connection, schema_name: str, relation_name: str) -> bool:
    result = connection.execute(
        """
        SELECT count(*)
        FROM information_schema.tables
        WHERE table_schema = ? AND table_name = ?
        """,
        [schema_name, relation_name],
    ).fetchone()
    return bool(result and result[0])


def relation_dimensions(connection, schema_name: str, relation_name: str) -> tuple[int, int]:
    qualified = f"{quote_identifier(schema_name)}.{quote_identifier(relation_name)}"
    row_count = int(connection.execute(f"SELECT count(*) FROM {qualified}").fetchone()[0])
    column_count = int(
        connection.execute(
            """
            SELECT count(*)
            FROM information_schema.columns
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema_name, relation_name],
        ).fetchone()[0]
    )
    return row_count, column_count


def initialize_database(connection, temp_directory: Path, memory_limit: str, threads: int) -> None:
    temp_directory.mkdir(parents=True, exist_ok=True)
    escaped_temp = str(temp_directory).replace("'", "''")
    connection.execute(f"SET temp_directory = '{escaped_temp}'")
    connection.execute(f"SET memory_limit = '{memory_limit}'")
    connection.execute(f"SET threads = {int(threads)}")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("CREATE SCHEMA IF NOT EXISTS meta")
    for schema_name in SOURCE_SCHEMAS.values():
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema_name)}")
    connection.execute("CREATE SCHEMA IF NOT EXISTS analysis")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS meta.load_manifest (
            source VARCHAR NOT NULL,
            catalog_code VARCHAR NOT NULL,
            dataset VARCHAR NOT NULL,
            schema_name VARCHAR NOT NULL,
            table_name VARCHAR NOT NULL,
            source_path VARCHAR NOT NULL,
            source_bytes BIGINT NOT NULL,
            source_sha256 VARCHAR NOT NULL,
            expected_rows BIGINT NOT NULL,
            loaded_rows BIGINT NOT NULL,
            expected_columns INTEGER NOT NULL,
            loaded_columns INTEGER NOT NULL,
            access_tier VARCHAR NOT NULL,
            loaded_at VARCHAR NOT NULL,
            PRIMARY KEY (schema_name, table_name)
        )
        """
    )


def manifest_record(connection, schema_name: str, relation_name: str):
    return connection.execute(
        """
        SELECT source_sha256, expected_rows, loaded_rows, expected_columns, loaded_columns
        FROM meta.load_manifest
        WHERE schema_name = ? AND table_name = ?
        """,
        [schema_name, relation_name],
    ).fetchone()


def table_is_current(connection, row: dict[str, str]) -> bool:
    schema_name, relation_name = table_key(row)
    if not relation_exists(connection, schema_name, relation_name):
        return False
    record = manifest_record(connection, schema_name, relation_name)
    if record is None:
        return False
    expected_loaded_columns = int(row["column_count"]) + len(PROVENANCE_COLUMNS)
    expected = (
        row["output_sha256"],
        int(row["row_count"]),
        int(row["row_count"]),
        expected_loaded_columns,
        expected_loaded_columns,
    )
    if tuple(record) != expected:
        return False
    return relation_dimensions(connection, schema_name, relation_name) == (
        int(row["row_count"]),
        expected_loaded_columns,
    )


def load_panel(connection, row: dict[str, str], repo_root: Path) -> dict[str, object]:
    schema_name, relation_name = table_key(row)
    qualified = f"{quote_identifier(schema_name)}.{quote_identifier(relation_name)}"
    temporary_name = f"__loading_{relation_name}"
    temporary_qualified = f"{quote_identifier(schema_name)}.{quote_identifier(temporary_name)}"
    source_path = repo_root / row["output_path"]
    expected_rows = int(row["row_count"])
    expected_columns = int(row["column_count"]) + len(PROVENANCE_COLUMNS)
    started = time.monotonic()

    connection.execute(f"DROP TABLE IF EXISTS {temporary_qualified}")
    connection.execute(
        f"""
        CREATE TABLE {temporary_qualified} AS
        SELECT *
        FROM read_csv(
            ?,
            header = true,
            all_varchar = true,
            compression = 'gzip',
            encoding = 'utf-8',
            delim = ',',
            quote = '"',
            escape = '"',
            nullstr = '{NULL_SENTINEL}',
            strict_mode = true
        )
        """,
        [str(source_path)],
    )
    loaded_rows, loaded_columns = relation_dimensions(connection, schema_name, temporary_name)
    if loaded_rows != expected_rows or loaded_columns != expected_columns:
        connection.execute(f"DROP TABLE {temporary_qualified}")
        raise RuntimeError(
            f"loaded dimensions mismatch for {qualified}: "
            f"expected {expected_rows}x{expected_columns}, got {loaded_rows}x{loaded_columns}"
        )

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(f"DROP TABLE IF EXISTS {qualified}")
        connection.execute(
            f"ALTER TABLE {temporary_qualified} RENAME TO {quote_identifier(relation_name)}"
        )
        connection.execute(
            "DELETE FROM meta.load_manifest WHERE schema_name = ? AND table_name = ?",
            [schema_name, relation_name],
        )
        connection.execute(
            """
            INSERT INTO meta.load_manifest VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["source"],
                row["catalog_code"],
                row["dataset"],
                schema_name,
                relation_name,
                row["output_path"],
                int(row["output_bytes"]),
                row["output_sha256"],
                expected_rows,
                loaded_rows,
                expected_columns,
                loaded_columns,
                row["access_tier"],
                utc_now(),
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {
        "schema": schema_name,
        "table": relation_name,
        "rows": loaded_rows,
        "columns": loaded_columns,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def replace_panel_catalog(connection, rows: list[dict[str, str]]) -> None:
    connection.execute("DROP TABLE IF EXISTS meta.panel_catalog")
    connection.execute(
        """
        CREATE TABLE meta.panel_catalog (
            source VARCHAR,
            catalog_code VARCHAR,
            dataset VARCHAR,
            schema_name VARCHAR,
            table_name VARCHAR,
            access_tier VARCHAR,
            physical_domn_codes VARCHAR,
            row_count BIGINT,
            domain_column_count INTEGER,
            loaded_column_count INTEGER,
            first_year VARCHAR,
            last_year VARCHAR,
            input_archive_count INTEGER,
            output_path VARCHAR,
            output_bytes BIGINT,
            output_sha256 VARCHAR
        )
        """
    )
    values = []
    for row in rows:
        schema_name, relation_name = table_key(row)
        values.append(
            (
                row["source"],
                row["catalog_code"],
                row["dataset"],
                schema_name,
                relation_name,
                row["access_tier"],
                row.get("physical_domn_codes", ""),
                int(row["row_count"]),
                int(row["column_count"]),
                int(row["column_count"]) + len(PROVENANCE_COLUMNS),
                row.get("first_year", ""),
                row.get("last_year", ""),
                int(row.get("input_archive_count", "0") or 0),
                row["output_path"],
                int(row["output_bytes"]),
                row["output_sha256"],
            )
        )
    connection.executemany("INSERT INTO meta.panel_catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)


def load_employment_analysis_views(
    connection,
    source_path: Path,
    resolution_summary_path: Path,
) -> dict[str, object]:
    """Load the standalone aggregate and enforce the legacy year boundary."""

    if not source_path.is_file():
        raise RuntimeError(f"standalone employment panel is missing: {source_path}")
    summary = json.loads(resolution_summary_path.read_text(encoding="utf-8"))
    if summary["status"] != "complete_with_scope_exclusion":
        raise RuntimeError("employment scope exclusion is not complete")
    employment_summary = summary["employment"]
    if employment_summary["legacy_panel_eligible_row_count"] != 0:
        raise RuntimeError("employment scope summary still allows legacy-eligible rows")
    expected_output = summary["outputs"]["derived_employment"]
    expected_sha256 = expected_output["sha256"]
    actual_sha256 = sha256_file(source_path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            "standalone employment checksum does not match identity-resolution summary"
        )

    with gzip.open(source_path, "rt", encoding="utf-8", newline="") as handle:
        source_fields = next(csv.reader(handle))
    if "개방ID" in source_fields:
        raise RuntimeError("standalone employment source unexpectedly has canonical OpenID")
    if "_open_id_candidate" not in source_fields:
        raise RuntimeError("standalone employment source lacks the expected audit candidate field")

    schema_name = "employment"
    relation_name = "safe_2023_2024_standalone"
    temporary_name = "__loading_safe_2023_2024_standalone"
    qualified = f"{quote_identifier(schema_name)}.{quote_identifier(relation_name)}"
    temporary_qualified = f"{quote_identifier(schema_name)}.{quote_identifier(temporary_name)}"
    connection.execute(f"DROP TABLE IF EXISTS {temporary_qualified}")
    connection.execute(
        f"""
        CREATE TABLE {temporary_qualified} AS
        SELECT * EXCLUDE (_open_id_candidate) FROM read_csv(
            ?, header = true, all_varchar = true, compression = 'gzip',
            encoding = 'utf-8', nullstr = '{NULL_SENTINEL}', strict_mode = true
        )
        """,
        [str(source_path)],
    )
    rows, columns = relation_dimensions(connection, schema_name, temporary_name)
    expected_rows = int(expected_output["row_count"])
    expected_columns = len(source_fields) - 1
    if (rows, columns) != (expected_rows, expected_columns):
        connection.execute(f"DROP TABLE {temporary_qualified}")
        raise RuntimeError(
            "standalone employment dimensions do not match identity-resolution summary"
        )
    years = [
        row[0]
        for row in connection.execute(
            f"SELECT DISTINCT _panel_year FROM {temporary_qualified} ORDER BY 1"
        ).fetchall()
    ]
    if years != ["2023", "2024"]:
        connection.execute(f"DROP TABLE {temporary_qualified}")
        raise RuntimeError(f"unexpected standalone employment years: {years}")
    blank_school_rows = int(
        connection.execute(
            f"SELECT count(*) FROM {temporary_qualified} WHERE coalesce(학교명, '') = ''"
        ).fetchone()[0]
    )
    if blank_school_rows != 0:
        connection.execute(f"DROP TABLE {temporary_qualified}")
        raise RuntimeError("standalone employment contains blank school names")

    if not relation_exists(connection, "employment", "panel_0001"):
        connection.execute(f"DROP TABLE {temporary_qualified}")
        raise RuntimeError("historical employment panel is required for analysis views")

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(f"DROP VIEW IF EXISTS analysis.employment_2023_2024_resolved")
        connection.execute(f"DROP VIEW IF EXISTS analysis.employment_2023_2024_standalone")
        connection.execute(f"DROP VIEW IF EXISTS analysis.employment_legacy_2010_2022")
        connection.execute("DROP TABLE IF EXISTS employment.safe_2023_2024_resolved")
        connection.execute(f"DROP TABLE IF EXISTS {qualified}")
        connection.execute(
            f"ALTER TABLE {temporary_qualified} RENAME TO {quote_identifier(relation_name)}"
        )
        connection.execute(
            f"CREATE VIEW analysis.employment_2023_2024_standalone AS SELECT * FROM {qualified}"
        )
        connection.execute(
            """
            CREATE VIEW analysis.employment_legacy_2010_2022 AS
            SELECT *
            FROM employment.panel_0001
            WHERE _panel_year BETWEEN '2010' AND '2022'
            """
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    legacy_rows, legacy_columns = relation_dimensions(
        connection,
        "analysis",
        "employment_legacy_2010_2022",
    )
    legacy_years = connection.execute(
        """
        SELECT min(_panel_year), max(_panel_year),
               count(*) FILTER (WHERE _panel_year IN ('2023', '2024')),
               count(*) FILTER (WHERE coalesce(개방ID, '') = '')
        FROM analysis.employment_legacy_2010_2022
        """
    ).fetchone()
    expected_legacy_rows = int(
        connection.execute("SELECT count(*) FROM employment.panel_0001").fetchone()[0]
    ) - expected_rows
    if legacy_rows != expected_legacy_rows:
        raise RuntimeError(
            "legacy employment row count does not reconcile with the scope exclusion"
        )
    if legacy_years != ("2010", "2022", 0, 0):
        raise RuntimeError(f"legacy employment boundary validation failed: {legacy_years}")

    return {
        "status": "complete_with_scope_exclusion",
        "legacy": {
            "view": "analysis.employment_legacy_2010_2022",
            "rows": legacy_rows,
            "columns": legacy_columns,
            "first_year": legacy_years[0],
            "last_year": legacy_years[1],
            "scope_excluded_year_rows": legacy_years[2],
            "missing_open_id_rows": legacy_years[3],
        },
        "standalone": {
            "schema": schema_name,
            "table": relation_name,
            "view": "analysis.employment_2023_2024_standalone",
            "rows": rows,
            "columns": columns,
            "years": years,
            "canonical_open_id_column_present": False,
            "candidate_open_id_column_present": False,
            "blank_school_name_rows": blank_school_rows,
            "sha256": actual_sha256,
        },
        "removed_default_analysis_view": "analysis.employment_2023_2024_resolved",
    }


def build_school_year_core_mart(
    connection,
    bridge_path: Path,
    bridge_summary_path: Path,
    dictionary_path: Path,
) -> dict[str, object]:
    """Build a one-row-per-school-year mart without joining raw 0101 directly."""

    if not bridge_path.is_file():
        raise RuntimeError(f"school-year bridge is missing: {bridge_path}")
    bridge_summary = json.loads(bridge_summary_path.read_text(encoding="utf-8"))
    bridge_validation = bridge_summary["bridge_validation"]
    if bridge_validation["unique_key"] is not True:
        raise RuntimeError("school-year bridge summary does not certify a unique key")

    bridge_temp = "meta.__loading_school_year_bridge"
    mart_temp = "analysis.__loading_school_year_core_2010_2022"
    connection.execute(f"DROP TABLE IF EXISTS {bridge_temp}")
    connection.execute(f"DROP TABLE IF EXISTS {mart_temp}")
    connection.execute(
        f"""
        CREATE TABLE {bridge_temp} AS
        SELECT * FROM read_csv(
            ?, header = true, all_varchar = true, encoding = 'utf-8',
            nullstr = '{NULL_SENTINEL}', strict_mode = true
        )
        """,
        [str(bridge_path)],
    )
    bridge_rows, bridge_columns = relation_dimensions(
        connection,
        "meta",
        "__loading_school_year_bridge",
    )
    bridge_key_stats = connection.execute(
        f"""
        SELECT count(*), count(DISTINCT (_panel_year, 개방ID)),
               count(*) FILTER (
                   WHERE coalesce(_panel_year, '') = '' OR coalesce(개방ID, '') = ''
               )
        FROM {bridge_temp}
        """
    ).fetchone()
    expected_bridge_rows = int(bridge_validation["row_count"])
    if bridge_rows != expected_bridge_rows or bridge_key_stats != (
        expected_bridge_rows,
        expected_bridge_rows,
        0,
    ):
        raise RuntimeError("school-year bridge failed row or key validation")

    source_columns = {
        row[0]
        for row in connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'higher_education' AND table_name = 'panel_0101'
            """
        ).fetchall()
    }
    required_source_columns = {
        "_panel_year",
        "개방ID",
        *(source for source, _output in SCHOOL_YEAR_CORE_METRICS),
    }
    missing_source_columns = sorted(required_source_columns - source_columns)
    if missing_source_columns:
        raise RuntimeError(
            f"0101 is missing school-year core fields: {missing_source_columns}"
        )

    metric_validation = {}
    for source_field, output_field in SCHOOL_YEAR_CORE_METRICS:
        source_identifier = quote_identifier(source_field)
        invalid_rows, negative_rows, source_sum = connection.execute(
            f"""
            SELECT
                count(*) FILTER (
                    WHERE coalesce(trim({source_identifier}), '') <> ''
                      AND try_cast(replace(trim({source_identifier}), ',', '') AS BIGINT)
                          IS NULL
                ),
                count(*) FILTER (
                    WHERE try_cast(replace(trim({source_identifier}), ',', '') AS BIGINT) < 0
                ),
                sum(try_cast(replace(trim({source_identifier}), ',', '') AS BIGINT))
            FROM higher_education.panel_0101
            WHERE _panel_year BETWEEN '2010' AND '2022'
            """
        ).fetchone()
        if invalid_rows or negative_rows:
            raise RuntimeError(
                f"invalid 0101 metric values for {source_field}: "
                f"invalid={invalid_rows}, negative={negative_rows}"
            )
        metric_validation[output_field] = {
            "source_field": source_field,
            "invalid_nonblank_row_count": invalid_rows,
            "negative_row_count": negative_rows,
            "source_sum": int(source_sum) if source_sum is not None else None,
        }

    aggregate_expressions = ",\n".join(
        (
            "sum(try_cast(replace(trim("
            f"{quote_identifier(source)}), ',', '') AS BIGINT)) AS "
            f"{quote_identifier(output)}"
        )
        for source, output in SCHOOL_YEAR_CORE_METRICS
    )
    metric_select = ",\n".join(
        f"a.{quote_identifier(output)}" for _source, output in SCHOOL_YEAR_CORE_METRICS
    )
    connection.execute(
        f"""
        CREATE TABLE {mart_temp} AS
        WITH aggregated_0101 AS (
            SELECT
                _panel_year,
                개방ID,
                count(*)::INTEGER AS metric_source_row_count,
                {aggregate_expressions}
            FROM higher_education.panel_0101
            WHERE _panel_year BETWEEN '2010' AND '2022'
            GROUP BY _panel_year, 개방ID
        )
        SELECT
            b._panel_year,
            b.개방ID,
            b._0101_exists,
            b._0101_match_status,
            b._review_status,
            try_cast(b._0101_source_row_count AS INTEGER) AS source_0101_row_count,
            try_cast(b._0101_branch_count AS INTEGER) AS branch_count,
            b._0101_branch_names AS branch_names,
            try_cast(b._0101_province_count AS INTEGER) AS province_count,
            b._0101_provinces AS provinces,
            try_cast(b._0101_region_count AS INTEGER) AS region_count,
            b._0101_regions AS regions,
            try_cast(b._0101_school_type_count AS INTEGER) AS school_type_count,
            b._0101_school_types AS school_types,
            b._0101_campus_scope AS campus_scope,
            try_cast(b._source_dataset_count AS INTEGER) AS observed_dataset_count,
            b._source_catalog_codes AS observed_catalog_codes,
            try_cast(b._source_row_count AS BIGINT) AS observed_source_row_count,
            a.metric_source_row_count,
            {metric_select}
        FROM {bridge_temp} AS b
        LEFT JOIN aggregated_0101 AS a
          ON b._panel_year = a._panel_year
         AND b.개방ID = a.개방ID
        WHERE b._panel_year BETWEEN '2010' AND '2022'
        """
    )

    mart_rows, mart_columns = relation_dimensions(
        connection,
        "analysis",
        "__loading_school_year_core_2010_2022",
    )
    mart_stats = connection.execute(
        f"""
        SELECT
            count(*),
            count(DISTINCT (_panel_year, 개방ID)),
            count(*) FILTER (
                WHERE coalesce(_panel_year, '') = '' OR coalesce(개방ID, '') = ''
            ),
            count(*) FILTER (WHERE _0101_exists = 'true'),
            count(*) FILTER (WHERE _0101_exists = 'false'),
            count(*) FILTER (WHERE campus_scope = 'multiple_campuses'),
            min(_panel_year),
            max(_panel_year),
            sum(coalesce(metric_source_row_count, 0))
        FROM {mart_temp}
        """
    ).fetchone()
    expected_mart_rows = int(
        connection.execute(
            f"""
            SELECT count(*) FROM {bridge_temp}
            WHERE _panel_year BETWEEN '2010' AND '2022'
            """
        ).fetchone()[0]
    )
    expected_matched_rows = int(
        connection.execute(
            f"""
            SELECT count(*) FROM {bridge_temp}
            WHERE _panel_year BETWEEN '2010' AND '2022' AND _0101_exists = 'true'
            """
        ).fetchone()[0]
    )
    expected_multiple_campus_rows = int(
        connection.execute(
            f"""
            SELECT count(*) FROM {bridge_temp}
            WHERE _panel_year BETWEEN '2010' AND '2022'
              AND _0101_campus_scope = 'multiple_campuses'
            """
        ).fetchone()[0]
    )
    source_0101_rows = int(
        connection.execute(
            """
            SELECT count(*) FROM higher_education.panel_0101
            WHERE _panel_year BETWEEN '2010' AND '2022'
            """
        ).fetchone()[0]
    )
    source_0101_key_count = int(
        connection.execute(
            """
            SELECT count(DISTINCT (_panel_year, 개방ID))
            FROM higher_education.panel_0101
            WHERE _panel_year BETWEEN '2010' AND '2022'
            """
        ).fetchone()[0]
    )
    if source_0101_key_count != expected_matched_rows:
        raise RuntimeError(
            "0101 aggregated keys do not match the school-year bridge coverage: "
            f"{source_0101_key_count} != {expected_matched_rows}"
        )
    expected_stats = (
        expected_mart_rows,
        expected_mart_rows,
        0,
        expected_matched_rows,
        expected_mart_rows - expected_matched_rows,
        expected_multiple_campus_rows,
        "2010",
        "2022",
        source_0101_rows,
    )
    if mart_stats != expected_stats:
        raise RuntimeError(
            f"school-year core mart validation mismatch: {mart_stats} != {expected_stats}"
        )

    with dictionary_path.open(encoding="utf-8-sig", newline="") as handle:
        dictionary_rows = list(csv.DictReader(handle))
    dictionary_fields = [row["column_name"] for row in dictionary_rows]
    mart_schema = connection.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'analysis'
          AND table_name = '__loading_school_year_core_2010_2022'
        ORDER BY ordinal_position
        """
    ).fetchall()
    if dictionary_fields != [row[0] for row in mart_schema]:
        raise RuntimeError("school-year core data dictionary columns do not match the mart")
    dictionary_types = [row["data_type"] for row in dictionary_rows]
    if dictionary_types != [row[1] for row in mart_schema]:
        raise RuntimeError("school-year core data dictionary types do not match the mart")

    metric_names = [output for _source, output in SCHOOL_YEAR_CORE_METRICS]
    any_metric_null = " OR ".join(
        f"{quote_identifier(field)} IS NULL" for field in metric_names
    )
    any_metric_nonnull = " OR ".join(
        f"{quote_identifier(field)} IS NOT NULL" for field in metric_names
    )
    matched_missing_metrics, unmatched_populated_metrics = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE _0101_exists = 'true' AND ({any_metric_null})),
            count(*) FILTER (WHERE _0101_exists = 'false' AND ({any_metric_nonnull}))
        FROM {mart_temp}
        """
    ).fetchone()
    if matched_missing_metrics or unmatched_populated_metrics:
        raise RuntimeError("school-year core metric missingness does not follow 0101 coverage")

    subset_pairs = (
        ("female_enrolled_student_count", "enrolled_student_count"),
        ("female_entrant_count", "entrant_count"),
        ("female_graduate_count", "graduate_count"),
        ("female_faculty_count", "faculty_count"),
        ("female_staff_count", "staff_count"),
    )
    subset_violations = {}
    for subset, total in subset_pairs:
        violations = int(
            connection.execute(
                f"""
                SELECT count(*) FROM {mart_temp}
                WHERE {quote_identifier(subset)} > {quote_identifier(total)}
                """
            ).fetchone()[0]
        )
        if violations:
            raise RuntimeError(f"school-year core subset violation: {subset} > {total}")
        subset_violations[f"{subset}_le_{total}"] = violations

    for _source_field, output_field in SCHOOL_YEAR_CORE_METRICS:
        mart_sum = connection.execute(
            f"SELECT sum({quote_identifier(output_field)}) FROM {mart_temp}"
        ).fetchone()[0]
        mart_sum = int(mart_sum) if mart_sum is not None else None
        metric_validation[output_field]["mart_sum"] = mart_sum
        metric_validation[output_field]["sum_reconciles"] = (
            mart_sum == metric_validation[output_field]["source_sum"]
        )
        if not metric_validation[output_field]["sum_reconciles"]:
            raise RuntimeError(f"school-year core sum mismatch: {output_field}")

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "DROP VIEW IF EXISTS "
            "analysis.school_year_core_with_employment_cohort_2010_2020"
        )
        connection.execute(
            "DROP VIEW IF EXISTS "
            "analysis.school_year_core_with_employment_2010_2022"
        )
        connection.execute("DROP TABLE IF EXISTS analysis.school_year_core_2010_2022")
        connection.execute("DROP TABLE IF EXISTS meta.school_year_bridge")
        connection.execute(
            "ALTER TABLE meta.__loading_school_year_bridge RENAME TO school_year_bridge"
        )
        connection.execute(
            "ALTER TABLE analysis.__loading_school_year_core_2010_2022 "
            "RENAME TO school_year_core_2010_2022"
        )
        connection.execute("DROP TABLE IF EXISTS meta.school_year_core_summary")
        connection.execute(
            f"""
            CREATE TABLE meta.school_year_core_summary AS
            SELECT
                'complete'::VARCHAR AS status,
                {mart_rows}::BIGINT AS row_count,
                {expected_matched_rows}::BIGINT AS matched_0101_row_count,
                {expected_mart_rows - expected_matched_rows}::BIGINT
                    AS unmatched_0101_row_count,
                0::BIGINT AS duplicate_key_count,
                0::BIGINT AS join_expansion_count
            """
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    join_output_rows = int(
        connection.execute(
            """
            SELECT count(*)
            FROM meta.school_year_bridge AS b
            LEFT JOIN analysis.school_year_core_2010_2022 AS m
              ON b._panel_year = m._panel_year AND b.개방ID = m.개방ID
            WHERE b._panel_year BETWEEN '2010' AND '2022'
            """
        ).fetchone()[0]
    )
    if join_output_rows != mart_rows:
        raise RuntimeError("school-year core join expanded the bridge population")

    return {
        "status": "complete",
        "table": "analysis.school_year_core_2010_2022",
        "grain": "one row per (_panel_year, 개방ID)",
        "years": ["2010", "2022"],
        "row_count": mart_rows,
        "column_count": mart_columns,
        "distinct_key_count": mart_stats[1],
        "duplicate_key_count": mart_rows - mart_stats[1],
        "blank_key_count": mart_stats[2],
        "matched_0101_row_count": mart_stats[3],
        "unmatched_0101_row_count": mart_stats[4],
        "multiple_campus_row_count": mart_stats[5],
        "source_0101_row_count": source_0101_rows,
        "aggregated_0101_key_count": source_0101_key_count,
        "accounted_0101_source_row_count": mart_stats[8],
        "matched_rows_with_missing_metrics": matched_missing_metrics,
        "unmatched_rows_with_populated_metrics": unmatched_populated_metrics,
        "bridge_left_join_output_row_count": join_output_rows,
        "join_expansion_count": join_output_rows - mart_rows,
        "metric_validation": metric_validation,
        "subset_violations": subset_violations,
        "bridge": {
            "table": "meta.school_year_bridge",
            "row_count": bridge_rows,
            "column_count": bridge_columns,
            "sha256": sha256_file(bridge_path),
        },
        "data_dictionary": {
            "row_count": len(dictionary_rows),
            "sha256": sha256_file(dictionary_path),
        },
    }


def build_employment_school_year_mart(
    connection,
    dictionary_path: Path,
) -> dict[str, object]:
    """Aggregate restricted legacy employment rows before joining the core mart."""

    source_view = "analysis.employment_legacy_2010_2022"
    aggregate_temp = "analysis.__loading_employment_school_year_aggregates"
    mart_temp = "analysis.__loading_employment_school_year_2010_2022"
    required_columns = {
        "_panel_year",
        "개방ID",
        *(source for source, _output in EMPLOYMENT_SCHOOL_YEAR_METRICS),
    }
    source_columns = {
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
    missing_columns = sorted(required_columns - source_columns)
    if missing_columns:
        raise RuntimeError(
            f"legacy employment is missing school-year fields: {missing_columns}"
        )

    source_stats = connection.execute(
        f"""
        SELECT
            count(*),
            count(DISTINCT (_panel_year, 개방ID)),
            count(*) FILTER (
                WHERE coalesce(_panel_year, '') = '' OR coalesce(개방ID, '') = ''
            ),
            min(_panel_year),
            max(_panel_year),
            count(DISTINCT _panel_year)
        FROM {source_view}
        """
    ).fetchone()
    if source_stats[2:] != (0, "2010", "2022", 13):
        raise RuntimeError(f"unexpected legacy employment period or keys: {source_stats}")

    metric_validation = {}
    for source_field, output_field in EMPLOYMENT_SCHOOL_YEAR_METRICS:
        source_identifier = quote_identifier(source_field)
        nonblank_rows, invalid_rows, negative_rows, nonbinary_rows, source_sum = (
            connection.execute(
                f"""
                SELECT
                    count(*) FILTER (
                        WHERE coalesce(trim({source_identifier}), '') <> ''
                    ),
                    count(*) FILTER (
                        WHERE coalesce(trim({source_identifier}), '') <> ''
                          AND try_cast(
                              replace(trim({source_identifier}), ',', '') AS BIGINT
                          ) IS NULL
                    ),
                    count(*) FILTER (
                        WHERE try_cast(
                            replace(trim({source_identifier}), ',', '') AS BIGINT
                        ) < 0
                    ),
                    count(*) FILTER (
                        WHERE try_cast(
                            replace(trim({source_identifier}), ',', '') AS BIGINT
                        ) NOT IN (0, 1)
                    ),
                    sum(try_cast(
                        replace(trim({source_identifier}), ',', '') AS BIGINT
                    ))
                FROM {source_view}
                """
            ).fetchone()
        )
        if (
            nonblank_rows != source_stats[0]
            or invalid_rows
            or negative_rows
            or nonbinary_rows
        ):
            raise RuntimeError(
                f"invalid legacy employment metric {source_field}: "
                f"nonblank={nonblank_rows}, invalid={invalid_rows}, "
                f"negative={negative_rows}, nonbinary={nonbinary_rows}"
            )
        metric_validation[output_field] = {
            "source_field": source_field,
            "nonblank_row_count": nonblank_rows,
            "invalid_nonblank_row_count": invalid_rows,
            "negative_row_count": negative_rows,
            "nonbinary_row_count": nonbinary_rows,
            "source_sum": int(source_sum),
        }

    connection.execute(f"DROP TABLE IF EXISTS {aggregate_temp}")
    connection.execute(f"DROP TABLE IF EXISTS {mart_temp}")
    aggregate_expressions = ",\n".join(
        (
            "sum(try_cast(replace(trim("
            f"{quote_identifier(source)}), ',', '') AS BIGINT)) AS "
            f"{quote_identifier(output)}"
        )
        for source, output in EMPLOYMENT_SCHOOL_YEAR_METRICS
    )
    connection.execute(
        f"""
        CREATE TABLE {aggregate_temp} AS
        SELECT
            _panel_year,
            개방ID,
            count(*)::BIGINT AS source_record_count,
            {aggregate_expressions}
        FROM {source_view}
        GROUP BY _panel_year, 개방ID
        """
    )

    comparison_fields = [
        "source_record_count",
        *(output for _source, output in EMPLOYMENT_SCHOOL_YEAR_METRICS),
    ]
    exact_comparison = " AND ".join(
        f"a.{quote_identifier(field)} = b.{quote_identifier(field)}"
        for field in comparison_fields
    )
    duplicate_stats = connection.execute(
        f"""
        WITH a AS (
            SELECT * FROM {aggregate_temp} WHERE _panel_year = '2021'
        ),
        b AS (
            SELECT * FROM {aggregate_temp} WHERE _panel_year = '2022'
        )
        SELECT
            (SELECT count(*) FROM a),
            (SELECT count(*) FROM b),
            count(*),
            count(*) FILTER (WHERE {exact_comparison})
        FROM a
        JOIN b USING (개방ID)
        """
    ).fetchone()
    duplicate_detected = (
        duplicate_stats[0] > 0
        and duplicate_stats[0] == duplicate_stats[1]
        and duplicate_stats[0] == duplicate_stats[2]
        and duplicate_stats[0] == duplicate_stats[3]
    )

    duplicate_status = (
        "duplicate_of_2021_school_aggregate" if duplicate_detected else "as_reported"
    )
    connection.execute(
        f"""
        CREATE TABLE {mart_temp} AS
        SELECT
            _panel_year,
            개방ID,
            CASE
                WHEN _panel_year = '2022' THEN ?
                ELSE 'as_reported'
            END::VARCHAR AS employment_quality_status,
            CASE
                WHEN _panel_year = '2022' AND ? THEN false
                ELSE true
            END::BOOLEAN AS employment_time_comparison_eligible,
            CASE
                WHEN sum(reported_further_study_count)
                     OVER (PARTITION BY _panel_year) = 0
                    THEN 'all_zero_source_field'
                ELSE 'as_reported'
            END::VARCHAR AS further_study_quality_status,
            * EXCLUDE (_panel_year, 개방ID)
        FROM {aggregate_temp}
        """,
        [duplicate_status, duplicate_detected],
    )

    mart_rows, mart_columns = relation_dimensions(
        connection,
        "analysis",
        "__loading_employment_school_year_2010_2022",
    )
    mart_stats = connection.execute(
        f"""
        SELECT
            count(*),
            count(DISTINCT (_panel_year, 개방ID)),
            count(*) FILTER (
                WHERE coalesce(_panel_year, '') = '' OR coalesce(개방ID, '') = ''
            ),
            min(_panel_year),
            max(_panel_year),
            sum(source_record_count),
            count(*) FILTER (WHERE employment_time_comparison_eligible = false)
        FROM {mart_temp}
        """
    ).fetchone()
    expected_ineligible_rows = duplicate_stats[1] if duplicate_detected else 0
    expected_stats = (
        source_stats[1],
        source_stats[1],
        0,
        "2010",
        "2022",
        source_stats[0],
        expected_ineligible_rows,
    )
    if mart_stats != expected_stats:
        raise RuntimeError(
            f"employment school-year mart validation mismatch: "
            f"{mart_stats} != {expected_stats}"
        )

    zero_further_study_years = [
        row[0]
        for row in connection.execute(
            f"""
            SELECT _panel_year
            FROM {mart_temp}
            GROUP BY _panel_year
            HAVING sum(reported_further_study_count) = 0
            ORDER BY _panel_year
            """
        ).fetchall()
    ]

    with dictionary_path.open(encoding="utf-8-sig", newline="") as handle:
        dictionary_rows = list(csv.DictReader(handle))
    mart_schema = connection.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'analysis'
          AND table_name = '__loading_employment_school_year_2010_2022'
        ORDER BY ordinal_position
        """
    ).fetchall()
    dictionary_schema = [
        (row["column_name"], row["data_type"]) for row in dictionary_rows
    ]
    if dictionary_schema != mart_schema:
        raise RuntimeError("employment school-year dictionary does not match the mart")

    subset_checks = {
        "reported_graduate_count_eq_source_record_count": (
            "reported_graduate_count <> source_record_count"
        ),
        "reported_employed_count_le_reported_graduate_count": (
            "reported_employed_count > reported_graduate_count"
        ),
        "reported_health_insurance_employed_count_le_reported_employed_count": (
            "reported_health_insurance_employed_count > reported_employed_count"
        ),
        "reported_school_employed_count_le_reported_employed_count": (
            "reported_school_employed_count > reported_employed_count"
        ),
    }
    for _source, output in EMPLOYMENT_SCHOOL_YEAR_METRICS[4:]:
        subset_checks[f"{output}_le_reported_graduate_count"] = (
            f"{quote_identifier(output)} > reported_graduate_count"
        )
    subset_violations = {}
    for check_name, predicate in subset_checks.items():
        violations = int(
            connection.execute(
                f"SELECT count(*) FROM {mart_temp} WHERE {predicate}"
            ).fetchone()[0]
        )
        if violations:
            raise RuntimeError(f"employment school-year subset violation: {check_name}")
        subset_violations[check_name] = violations

    for _source_field, output_field in EMPLOYMENT_SCHOOL_YEAR_METRICS:
        mart_sum = connection.execute(
            f"SELECT sum({quote_identifier(output_field)}) FROM {mart_temp}"
        ).fetchone()[0]
        mart_sum = int(mart_sum)
        metric_validation[output_field]["mart_sum"] = mart_sum
        metric_validation[output_field]["sum_reconciles"] = (
            mart_sum == metric_validation[output_field]["source_sum"]
        )
        if not metric_validation[output_field]["sum_reconciles"]:
            raise RuntimeError(f"employment school-year sum mismatch: {output_field}")

    orphan_employment_keys = int(
        connection.execute(
            f"""
            SELECT count(*)
            FROM {mart_temp} AS e
            LEFT JOIN analysis.school_year_core_2010_2022 AS c
              ON e._panel_year = c._panel_year AND e.개방ID = c.개방ID
            WHERE c.개방ID IS NULL
            """
        ).fetchone()[0]
    )
    if orphan_employment_keys:
        raise RuntimeError(
            f"employment school-year keys missing from core: {orphan_employment_keys}"
        )

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "DROP VIEW IF EXISTS "
            "analysis.school_year_core_with_employment_2010_2022"
        )
        connection.execute(
            "DROP TABLE IF EXISTS analysis.employment_school_year_2010_2022"
        )
        connection.execute(
            "ALTER TABLE analysis.__loading_employment_school_year_2010_2022 "
            "RENAME TO employment_school_year_2010_2022"
        )
        connection.execute(f"DROP TABLE IF EXISTS {aggregate_temp}")
        connection.execute(
            """
            CREATE VIEW analysis.school_year_core_with_employment_2010_2022 AS
            SELECT
                c.*,
                CASE WHEN e.개방ID IS NULL THEN 'false' ELSE 'true' END::VARCHAR
                    AS _employment_exists,
                e.employment_quality_status,
                e.employment_time_comparison_eligible,
                e.further_study_quality_status,
                e.source_record_count AS employment_source_record_count,
                e.reported_graduate_count AS employment_reported_graduate_count,
                e.reported_employed_count AS employment_reported_employed_count,
                e.reported_health_insurance_employed_count
                    AS employment_reported_health_insurance_employed_count,
                e.reported_school_employed_count
                    AS employment_reported_school_employed_count,
                e.reported_further_study_count
                    AS employment_reported_further_study_count,
                e.reported_military_service_count
                    AS employment_reported_military_service_count,
                e.reported_employment_unavailable_count
                    AS employment_reported_employment_unavailable_count,
                e.reported_foreign_student_count
                    AS employment_reported_foreign_student_count,
                e.reported_excluded_count AS employment_reported_excluded_count,
                e.reported_other_count AS employment_reported_other_count,
                e.reported_unknown_count AS employment_reported_unknown_count
            FROM analysis.school_year_core_2010_2022 AS c
            LEFT JOIN analysis.employment_school_year_2010_2022 AS e
              ON c._panel_year = e._panel_year AND c.개방ID = e.개방ID
            """
        )
        connection.execute("DROP TABLE IF EXISTS meta.employment_school_year_summary")
        connection.execute(
            f"""
            CREATE TABLE meta.employment_school_year_summary AS
            SELECT
                'complete'::VARCHAR AS status,
                {mart_rows}::BIGINT AS row_count,
                {source_stats[0]}::BIGINT AS source_row_count,
                {orphan_employment_keys}::BIGINT AS orphan_key_count,
                {duplicate_stats[3]}::BIGINT AS duplicate_year_exact_match_key_count,
                {str(duplicate_detected).lower()}::BOOLEAN
                    AS duplicate_year_detected
            """
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    joined_stats = connection.execute(
        """
        SELECT
            count(*),
            count(DISTINCT (_panel_year, 개방ID)),
            count(*) FILTER (WHERE _employment_exists = 'true'),
            count(*) FILTER (WHERE _employment_exists = 'false')
        FROM analysis.school_year_core_with_employment_2010_2022
        """
    ).fetchone()
    core_rows = int(
        connection.execute(
            "SELECT count(*) FROM analysis.school_year_core_2010_2022"
        ).fetchone()[0]
    )
    expected_joined_stats = (
        core_rows,
        core_rows,
        mart_rows,
        core_rows - mart_rows,
    )
    if joined_stats != expected_joined_stats:
        raise RuntimeError(
            f"employment core join validation mismatch: "
            f"{joined_stats} != {expected_joined_stats}"
        )

    return {
        "status": "complete",
        "table": "analysis.employment_school_year_2010_2022",
        "joined_view": "analysis.school_year_core_with_employment_2010_2022",
        "grain": "one row per (_panel_year, 개방ID)",
        "years": ["2010", "2022"],
        "row_count": mart_rows,
        "column_count": mart_columns,
        "distinct_key_count": mart_stats[1],
        "duplicate_key_count": mart_rows - mart_stats[1],
        "blank_key_count": mart_stats[2],
        "source_row_count": source_stats[0],
        "accounted_source_row_count": mart_stats[5],
        "orphan_employment_key_count": orphan_employment_keys,
        "joined_core_row_count": joined_stats[0],
        "joined_core_distinct_key_count": joined_stats[1],
        "joined_core_employment_matched_count": joined_stats[2],
        "joined_core_employment_unmatched_count": joined_stats[3],
        "join_expansion_count": joined_stats[0] - core_rows,
        "metric_validation": metric_validation,
        "subset_violations": subset_violations,
        "quality_findings": {
            "duplicate_year_comparison": {
                "base_year": "2021",
                "comparison_year": "2022",
                "base_key_count": duplicate_stats[0],
                "comparison_key_count": duplicate_stats[1],
                "shared_key_count": duplicate_stats[2],
                "exact_metric_vector_match_key_count": duplicate_stats[3],
                "exact_duplicate_detected": duplicate_detected,
                "comparison_eligible": not duplicate_detected,
            },
            "all_zero_reported_further_study_years": zero_further_study_years,
            "official_employment_rate_derived": False,
        },
        "data_dictionary": {
            "row_count": len(dictionary_rows),
            "sha256": sha256_file(dictionary_path),
        },
    }


def build_employment_cohort_school_mart(
    connection,
    cohort_audit_path: Path,
    dictionary_path: Path,
) -> dict[str, object]:
    """Build the selected one-row-per-employment-cohort-and-school mart."""

    if not cohort_audit_path.is_file():
        raise RuntimeError(f"employment cohort audit is missing: {cohort_audit_path}")
    if not dictionary_path.is_file():
        raise RuntimeError(
            f"employment cohort data dictionary is missing: {dictionary_path}"
        )
    if not relation_exists(connection, "analysis", "employment_school_year_2010_2022"):
        raise RuntimeError("employment school-year mart is required for cohort selection")

    with cohort_audit_path.open(encoding="utf-8-sig", newline="") as handle:
        audit_rows = list(csv.DictReader(handle))
    required_fields = {
        "source_year",
        "inferred_cohort_year",
        "observation_reference_date",
        "observation_reference_date_basis",
        "cohort_use_status",
        "cohort_analysis_eligible",
    }
    if not audit_rows or not required_fields.issubset(audit_rows[0]):
        missing = sorted(required_fields - set(audit_rows[0] if audit_rows else {}))
        raise RuntimeError(f"employment cohort audit fields are missing: {missing}")

    selected_rows = [
        row
        for row in audit_rows
        if row["cohort_analysis_eligible"].strip().lower() == "true"
    ]
    actual_selection = {
        row["source_year"]: (
            row["inferred_cohort_year"],
            row["observation_reference_date_basis"],
            row["cohort_use_status"],
        )
        for row in selected_rows
    }
    if actual_selection != EMPLOYMENT_COHORT_EXPECTED_SELECTION:
        raise RuntimeError(
            "employment cohort audit selection differs from the approved mapping: "
            f"{actual_selection}"
        )
    if len({row["inferred_cohort_year"] for row in selected_rows}) != len(selected_rows):
        raise RuntimeError("employment cohort audit selected more than one source per cohort")

    map_temp = "meta.__loading_employment_cohort_source_map"
    mart_temp = "analysis.__loading_employment_cohort_school_2010_2020"
    connection.execute(f"DROP TABLE IF EXISTS {map_temp}")
    connection.execute(f"DROP TABLE IF EXISTS {mart_temp}")
    connection.execute(
        f"""
        CREATE TABLE {map_temp} (
            employment_source_panel_year VARCHAR NOT NULL,
            employment_cohort_year VARCHAR NOT NULL,
            employment_reference_date VARCHAR NOT NULL,
            employment_reference_date_basis VARCHAR NOT NULL,
            employment_cohort_selection_status VARCHAR NOT NULL
        )
        """
    )
    connection.executemany(
        f"INSERT INTO {map_temp} VALUES (?, ?, ?, ?, ?)",
        [
            (
                row["source_year"],
                row["inferred_cohort_year"],
                row["observation_reference_date"],
                row["observation_reference_date_basis"],
                row["cohort_use_status"],
            )
            for row in selected_rows
        ],
    )
    connection.execute(
        f"""
        CREATE TABLE {mart_temp} AS
        SELECT
            m.employment_cohort_year,
            m.employment_source_panel_year,
            e.개방ID,
            m.employment_reference_date,
            m.employment_reference_date_basis,
            m.employment_cohort_selection_status,
            CASE
                WHEN m.employment_reference_date_basis = 'june_1'
                    THEN 'june_1_pre_unification'
                WHEN m.employment_source_panel_year = '2015'
                    THEN 'december_31_transition_selected'
                ELSE 'december_31_post_unification'
            END::VARCHAR AS employment_comparability_regime,
            e.employment_quality_status,
            true::BOOLEAN AS employment_cohort_selected,
            e.further_study_quality_status,
            e.source_record_count,
            e.reported_graduate_count,
            e.reported_employed_count,
            e.reported_health_insurance_employed_count,
            e.reported_school_employed_count,
            e.reported_further_study_count,
            e.reported_military_service_count,
            e.reported_employment_unavailable_count,
            e.reported_foreign_student_count,
            e.reported_excluded_count,
            e.reported_other_count,
            e.reported_unknown_count
        FROM analysis.employment_school_year_2010_2022 AS e
        JOIN {map_temp} AS m
          ON e._panel_year = m.employment_source_panel_year
        """
    )

    mart_rows, mart_columns = relation_dimensions(
        connection,
        "analysis",
        "__loading_employment_cohort_school_2010_2020",
    )
    mart_stats = connection.execute(
        f"""
        SELECT
            count(*),
            count(DISTINCT (employment_cohort_year, 개방ID)),
            count(*) FILTER (
                WHERE coalesce(employment_cohort_year, '') = ''
                   OR coalesce(employment_source_panel_year, '') = ''
                   OR coalesce(개방ID, '') = ''
            ),
            min(employment_cohort_year),
            max(employment_cohort_year),
            count(DISTINCT employment_cohort_year),
            sum(source_record_count),
            count(*) FILTER (
                WHERE employment_source_panel_year IN ('2014', '2022')
            )
        FROM {mart_temp}
        """
    ).fetchone()
    selected_source_records = int(
        connection.execute(
            f"""
            SELECT sum(e.source_record_count)
            FROM analysis.employment_school_year_2010_2022 AS e
            JOIN {map_temp} AS m
              ON e._panel_year = m.employment_source_panel_year
            """
        ).fetchone()[0]
    )
    expected_stats = (
        mart_rows,
        mart_rows,
        0,
        "2010",
        "2020",
        11,
        selected_source_records,
        0,
    )
    if mart_stats != expected_stats:
        raise RuntimeError(
            f"employment cohort mart validation mismatch: {mart_stats} != {expected_stats}"
        )

    orphan_keys = int(
        connection.execute(
            f"""
            SELECT count(*)
            FROM {mart_temp} AS e
            LEFT JOIN analysis.school_year_core_2010_2022 AS c
              ON c._panel_year = e.employment_cohort_year
             AND c.개방ID = e.개방ID
            WHERE c.개방ID IS NULL
            """
        ).fetchone()[0]
    )
    if orphan_keys:
        raise RuntimeError(f"employment cohort keys missing from core: {orphan_keys}")

    metric_validation = {}
    for _source_field, output_field in EMPLOYMENT_SCHOOL_YEAR_METRICS:
        selected_sum = int(
            connection.execute(
                f"""
                SELECT sum(e.{quote_identifier(output_field)})
                FROM analysis.employment_school_year_2010_2022 AS e
                JOIN {map_temp} AS m
                  ON e._panel_year = m.employment_source_panel_year
                """
            ).fetchone()[0]
        )
        mart_sum = int(
            connection.execute(
                f"SELECT sum({quote_identifier(output_field)}) FROM {mart_temp}"
            ).fetchone()[0]
        )
        if selected_sum != mart_sum:
            raise RuntimeError(f"employment cohort sum mismatch: {output_field}")
        metric_validation[output_field] = {
            "selected_source_sum": selected_sum,
            "cohort_mart_sum": mart_sum,
            "sum_reconciles": True,
        }

    with dictionary_path.open(encoding="utf-8-sig", newline="") as handle:
        dictionary_rows = list(csv.DictReader(handle))
    mart_schema = connection.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'analysis'
          AND table_name = '__loading_employment_cohort_school_2010_2020'
        ORDER BY ordinal_position
        """
    ).fetchall()
    dictionary_schema = [
        (row["column_name"], row["data_type"]) for row in dictionary_rows
    ]
    if dictionary_schema != mart_schema:
        raise RuntimeError("employment cohort dictionary does not match the mart")

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "DROP VIEW IF EXISTS "
            "analysis.school_year_core_with_employment_cohort_2010_2020"
        )
        connection.execute(
            "DROP TABLE IF EXISTS analysis.employment_cohort_school_2010_2020"
        )
        connection.execute("DROP TABLE IF EXISTS meta.employment_cohort_source_map")
        connection.execute(
            "ALTER TABLE analysis.__loading_employment_cohort_school_2010_2020 "
            "RENAME TO employment_cohort_school_2010_2020"
        )
        connection.execute(
            "ALTER TABLE meta.__loading_employment_cohort_source_map "
            "RENAME TO employment_cohort_source_map"
        )
        connection.execute(
            """
            CREATE VIEW analysis.school_year_core_with_employment_cohort_2010_2020 AS
            SELECT
                c.*,
                CASE WHEN e.개방ID IS NULL THEN 'false' ELSE 'true' END::VARCHAR
                    AS _employment_cohort_exists,
                e.employment_source_panel_year,
                e.employment_reference_date,
                e.employment_reference_date_basis,
                e.employment_cohort_selection_status,
                e.employment_comparability_regime,
                e.employment_quality_status,
                e.employment_cohort_selected,
                e.further_study_quality_status,
                e.source_record_count AS employment_source_record_count,
                e.reported_graduate_count AS employment_reported_graduate_count,
                e.reported_employed_count AS employment_reported_employed_count,
                e.reported_health_insurance_employed_count
                    AS employment_reported_health_insurance_employed_count,
                e.reported_school_employed_count
                    AS employment_reported_school_employed_count,
                e.reported_further_study_count
                    AS employment_reported_further_study_count,
                e.reported_military_service_count
                    AS employment_reported_military_service_count,
                e.reported_employment_unavailable_count
                    AS employment_reported_employment_unavailable_count,
                e.reported_foreign_student_count
                    AS employment_reported_foreign_student_count,
                e.reported_excluded_count AS employment_reported_excluded_count,
                e.reported_other_count AS employment_reported_other_count,
                e.reported_unknown_count AS employment_reported_unknown_count
            FROM analysis.school_year_core_2010_2022 AS c
            LEFT JOIN analysis.employment_cohort_school_2010_2020 AS e
              ON c._panel_year = e.employment_cohort_year
             AND c.개방ID = e.개방ID
            WHERE c._panel_year BETWEEN '2010' AND '2020'
            """
        )
        connection.execute("DROP TABLE IF EXISTS meta.employment_cohort_summary")
        connection.execute(
            f"""
            CREATE TABLE meta.employment_cohort_summary AS
            SELECT
                'complete'::VARCHAR AS status,
                {mart_rows}::BIGINT AS row_count,
                {selected_source_records}::BIGINT AS selected_source_row_count,
                11::BIGINT AS cohort_year_count,
                {orphan_keys}::BIGINT AS orphan_key_count,
                0::BIGINT AS duplicate_key_count
            """
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise

    joined_stats = connection.execute(
        """
        SELECT
            count(*),
            count(DISTINCT (_panel_year, 개방ID)),
            count(*) FILTER (WHERE _employment_cohort_exists = 'true'),
            count(*) FILTER (WHERE _employment_cohort_exists = 'false')
        FROM analysis.school_year_core_with_employment_cohort_2010_2020
        """
    ).fetchone()
    core_cohort_rows = int(
        connection.execute(
            """
            SELECT count(*)
            FROM analysis.school_year_core_2010_2022
            WHERE _panel_year BETWEEN '2010' AND '2020'
            """
        ).fetchone()[0]
    )
    expected_joined_stats = (
        core_cohort_rows,
        core_cohort_rows,
        mart_rows,
        core_cohort_rows - mart_rows,
    )
    if joined_stats != expected_joined_stats:
        raise RuntimeError(
            f"employment cohort core join mismatch: {joined_stats} != {expected_joined_stats}"
        )

    return {
        "status": "complete",
        "table": "analysis.employment_cohort_school_2010_2020",
        "joined_view": "analysis.school_year_core_with_employment_cohort_2010_2020",
        "mapping_table": "meta.employment_cohort_source_map",
        "grain": "one row per (employment_cohort_year, 개방ID)",
        "cohort_years": ["2010", "2020"],
        "source_panel_years": ["2010", "2021"],
        "excluded_source_panel_years": ["2014", "2022"],
        "row_count": mart_rows,
        "column_count": mart_columns,
        "distinct_key_count": mart_stats[1],
        "duplicate_key_count": mart_rows - mart_stats[1],
        "blank_key_count": mart_stats[2],
        "selected_source_row_count": selected_source_records,
        "orphan_employment_key_count": orphan_keys,
        "joined_core_row_count": joined_stats[0],
        "joined_core_distinct_key_count": joined_stats[1],
        "joined_core_employment_matched_count": joined_stats[2],
        "joined_core_employment_unmatched_count": joined_stats[3],
        "join_expansion_count": joined_stats[0] - core_cohort_rows,
        "reference_date_regime_break": {
            "last_june_1_cohort": "2013",
            "first_selected_december_31_cohort": "2014",
            "cross_regime_comparison_requires_caveat": True,
        },
        "metric_validation": metric_validation,
        "cohort_audit": {
            "row_count": len(audit_rows),
            "selected_source_year_count": len(selected_rows),
            "sha256": sha256_file(cohort_audit_path),
        },
        "data_dictionary": {
            "row_count": len(dictionary_rows),
            "sha256": sha256_file(dictionary_path),
        },
    }


def replace_build_summary(
    connection,
    rows: list[dict[str, str]],
    employment_views: dict[str, object],
    school_year_core: dict[str, object],
    employment_school_year: dict[str, object],
    employment_cohort_school: dict[str, object],
    status: str,
) -> None:
    connection.execute("DROP TABLE IF EXISTS meta.database_summary")
    connection.execute(
        """
        CREATE TABLE meta.database_summary AS
        SELECT
            ?::VARCHAR AS status,
            ?::BIGINT AS panel_table_count,
            ?::BIGINT AS panel_row_count,
            ?::BIGINT AS legacy_employment_rows,
            ?::BIGINT AS standalone_employment_rows,
            ?::BIGINT AS scope_excluded_employment_rows,
            ?::BIGINT AS school_year_core_rows,
            ?::BIGINT AS employment_school_year_rows,
            ?::BIGINT AS employment_cohort_school_rows
        """,
        [
            status,
            len(rows),
            sum(int(row["row_count"]) for row in rows),
            int(employment_views["legacy"]["rows"]),
            int(employment_views["standalone"]["rows"]),
            int(employment_views["standalone"]["rows"]),
            int(school_year_core["row_count"]),
            int(employment_school_year["row_count"]),
            int(employment_cohort_school["row_count"]),
        ],
    )
    connection.execute(
        "CREATE OR REPLACE VIEW analysis.panel_inventory AS SELECT * FROM meta.panel_catalog"
    )


def atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--audit-output", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument(
        "--standalone-employment",
        type=Path,
        default=DEFAULT_STANDALONE_EMPLOYMENT,
    )
    parser.add_argument(
        "--identity-resolution-summary",
        type=Path,
        default=DEFAULT_IDENTITY_RESOLUTION_SUMMARY,
    )
    parser.add_argument(
        "--school-year-bridge",
        type=Path,
        default=DEFAULT_SCHOOL_YEAR_BRIDGE,
    )
    parser.add_argument(
        "--bridge-summary",
        type=Path,
        default=DEFAULT_BRIDGE_SUMMARY,
    )
    parser.add_argument(
        "--school-year-core-dictionary",
        type=Path,
        default=DEFAULT_SCHOOL_YEAR_CORE_DICTIONARY,
    )
    parser.add_argument(
        "--employment-school-year-dictionary",
        type=Path,
        default=DEFAULT_EMPLOYMENT_SCHOOL_YEAR_DICTIONARY,
    )
    parser.add_argument(
        "--employment-cohort-audit",
        type=Path,
        default=DEFAULT_EMPLOYMENT_COHORT_AUDIT,
    )
    parser.add_argument(
        "--employment-cohort-dictionary",
        type=Path,
        default=DEFAULT_EMPLOYMENT_COHORT_DICTIONARY,
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    parser.add_argument("--max-panels", type=int)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    catalog_path = args.catalog if args.catalog.is_absolute() else repo_root / args.catalog
    database_path = args.database if args.database.is_absolute() else repo_root / args.database
    audit_path = args.audit_output if args.audit_output.is_absolute() else repo_root / args.audit_output
    standalone_path = (
        args.standalone_employment
        if args.standalone_employment.is_absolute()
        else repo_root / args.standalone_employment
    )
    resolution_summary_path = (
        args.identity_resolution_summary
        if args.identity_resolution_summary.is_absolute()
        else repo_root / args.identity_resolution_summary
    )
    bridge_path = (
        args.school_year_bridge
        if args.school_year_bridge.is_absolute()
        else repo_root / args.school_year_bridge
    )
    bridge_summary_path = (
        args.bridge_summary
        if args.bridge_summary.is_absolute()
        else repo_root / args.bridge_summary
    )
    core_dictionary_path = (
        args.school_year_core_dictionary
        if args.school_year_core_dictionary.is_absolute()
        else repo_root / args.school_year_core_dictionary
    )
    employment_dictionary_path = (
        args.employment_school_year_dictionary
        if args.employment_school_year_dictionary.is_absolute()
        else repo_root / args.employment_school_year_dictionary
    )
    employment_cohort_audit_path = (
        args.employment_cohort_audit
        if args.employment_cohort_audit.is_absolute()
        else repo_root / args.employment_cohort_audit
    )
    employment_cohort_dictionary_path = (
        args.employment_cohort_dictionary
        if args.employment_cohort_dictionary.is_absolute()
        else repo_root / args.employment_cohort_dictionary
    )
    all_rows = read_catalog(catalog_path, repo_root)
    selected_rows = all_rows[: args.max_panels] if args.max_panels else all_rows
    status = "complete" if len(selected_rows) == len(all_rows) else "partial"

    duckdb = require_duckdb()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temp_directory = database_path.parent / "duckdb_tmp"
    connection = duckdb.connect(str(database_path))
    initialize_database(connection, temp_directory, args.memory_limit, args.threads)
    emit(
        {
            "event": "start",
            "duckdb_version": duckdb.__version__,
            "panel_count": len(selected_rows),
            "expected_rows": sum(int(row["row_count"]) for row in selected_rows),
            "database": str(args.database),
        }
    )

    loaded_count = 0
    skipped_count = 0
    started = time.monotonic()
    try:
        for index, row in enumerate(selected_rows, start=1):
            schema_name, relation_name = table_key(row)
            if table_is_current(connection, row):
                skipped_count += 1
                event = {
                    "event": "panel_skipped",
                    "index": index,
                    "total": len(selected_rows),
                    "schema": schema_name,
                    "table": relation_name,
                    "rows": int(row["row_count"]),
                }
            else:
                result = load_panel(connection, row, repo_root)
                loaded_count += 1
                event = {
                    "event": "panel_loaded",
                    "index": index,
                    "total": len(selected_rows),
                    **result,
                }
            elapsed = time.monotonic() - started
            event["elapsed_total_seconds"] = round(elapsed, 1)
            event["estimated_remaining_seconds"] = round(
                elapsed / index * (len(selected_rows) - index), 1
            )
            emit(event)
            if index % args.checkpoint_every == 0:
                connection.execute("CHECKPOINT")

        replace_panel_catalog(connection, selected_rows)
        employment_views = load_employment_analysis_views(
            connection,
            standalone_path,
            resolution_summary_path,
        )
        school_year_core = build_school_year_core_mart(
            connection,
            bridge_path,
            bridge_summary_path,
            core_dictionary_path,
        )
        employment_school_year = build_employment_school_year_mart(
            connection,
            employment_dictionary_path,
        )
        employment_cohort_school = build_employment_cohort_school_mart(
            connection,
            employment_cohort_audit_path,
            employment_cohort_dictionary_path,
        )
        replace_build_summary(
            connection,
            selected_rows,
            employment_views,
            school_year_core,
            employment_school_year,
            employment_cohort_school,
            status,
        )
        connection.execute("CHECKPOINT")
        manifest_rows = int(connection.execute("SELECT count(*) FROM meta.load_manifest").fetchone()[0])
        manifest_total_rows = int(
            connection.execute(
                """
                SELECT coalesce(sum(loaded_rows), 0)
                FROM meta.load_manifest
                WHERE (schema_name, table_name) IN (
                    SELECT schema_name, table_name FROM meta.panel_catalog
                )
                """
            ).fetchone()[0]
        )
    finally:
        connection.close()

    database_record = {
        "relative_path": args.database.as_posix(),
        "bytes": database_path.stat().st_size,
        "sha256": sha256_file(database_path),
    }
    source_counts = Counter(row["source"] for row in selected_rows)
    source_rows = {
        source: sum(int(row["row_count"]) for row in selected_rows if row["source"] == source)
        for source in sorted(source_counts)
    }
    audit = {
        "status": status,
        "generated_at": utc_now(),
        "duckdb_version": duckdb.__version__,
        "inputs": {
            "standalone_employment": {
                "relative_path": args.standalone_employment.as_posix(),
                "sha256": employment_views["standalone"]["sha256"],
            },
            "identity_resolution_summary": {
                "relative_path": args.identity_resolution_summary.as_posix(),
                "sha256": sha256_file(resolution_summary_path),
            },
            "school_year_bridge": {
                "relative_path": args.school_year_bridge.as_posix(),
                "sha256": school_year_core["bridge"]["sha256"],
            },
            "school_year_bridge_summary": {
                "relative_path": args.bridge_summary.as_posix(),
                "sha256": sha256_file(bridge_summary_path),
            },
            "school_year_core_data_dictionary": {
                "relative_path": args.school_year_core_dictionary.as_posix(),
                "sha256": school_year_core["data_dictionary"]["sha256"],
            },
            "employment_school_year_data_dictionary": {
                "relative_path": args.employment_school_year_dictionary.as_posix(),
                "sha256": employment_school_year["data_dictionary"]["sha256"],
            },
            "employment_cohort_year_audit": {
                "relative_path": args.employment_cohort_audit.as_posix(),
                "sha256": employment_cohort_school["cohort_audit"]["sha256"],
            },
            "employment_cohort_school_data_dictionary": {
                "relative_path": args.employment_cohort_dictionary.as_posix(),
                "sha256": employment_cohort_school["data_dictionary"]["sha256"],
            },
        },
        "database": database_record,
        "catalog": {
            "relative_path": args.catalog.as_posix(),
            "panel_table_count": len(selected_rows),
            "expected_panel_table_count": len(all_rows),
            "expected_panel_row_count": sum(int(row["row_count"]) for row in selected_rows),
            "source_table_counts": dict(sorted(source_counts.items())),
            "source_row_counts": source_rows,
        },
        "build": {
            "loaded_this_run": loaded_count,
            "skipped_current_this_run": skipped_count,
            "manifest_table_count": manifest_rows,
            "manifest_panel_row_count": manifest_total_rows,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        },
        "employment_analysis_views": employment_views,
        "school_year_core_mart": school_year_core,
        "employment_school_year_mart": employment_school_year,
        "employment_cohort_school_mart": employment_cohort_school,
        "validation": {
            "catalog_paths_missing": 0,
            "catalog_size_mismatches": 0,
            "panel_dimension_mismatches": 0,
            "blank_strings_preserved_as_empty_strings": True,
            "all_source_columns_loaded_as_varchar": True,
            "incompatible_panel_grains_concatenated": False,
            "legacy_employment_year_boundary_enforced": True,
            "standalone_employment_open_id_columns_removed": True,
            "schema_break_scope_excluded_rows": employment_views["standalone"]["rows"],
            "school_year_core_unique_key": school_year_core["duplicate_key_count"] == 0,
            "school_year_core_join_expansion_count": school_year_core["join_expansion_count"],
            "employment_school_year_unique_key": (
                employment_school_year["duplicate_key_count"] == 0
            ),
            "employment_school_year_orphan_key_count": (
                employment_school_year["orphan_employment_key_count"]
            ),
            "employment_core_join_expansion_count": (
                employment_school_year["join_expansion_count"]
            ),
            "employment_cohort_school_unique_key": (
                employment_cohort_school["duplicate_key_count"] == 0
            ),
            "employment_cohort_school_orphan_key_count": (
                employment_cohort_school["orphan_employment_key_count"]
            ),
            "employment_cohort_core_join_expansion_count": (
                employment_cohort_school["join_expansion_count"]
            ),
            "employment_2014_transition_december_wave_selected": True,
            "employment_2014_june_wave_excluded": True,
            "employment_2022_exact_repeat_excluded": True,
            "employment_2022_time_comparison_eligible": (
                employment_school_year["quality_findings"]
                ["duplicate_year_comparison"]["comparison_eligible"]
            ),
            "official_employment_rate_derived": False,
            "database_access_tier": "restricted",
        },
    }
    atomic_write_json(audit_path, audit)
    emit(
        {
            "event": "complete",
            "status": status,
            "panel_tables": len(selected_rows),
            "panel_rows": audit["catalog"]["expected_panel_row_count"],
            "database_bytes": database_record["bytes"],
            "elapsed_seconds": audit["build"]["elapsed_seconds"],
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.stderr.close()
        raise SystemExit(1)
