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


def replace_build_summary(
    connection,
    rows: list[dict[str, str]],
    employment_views: dict[str, object],
    school_year_core: dict[str, object],
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
            ?::BIGINT AS school_year_core_rows
        """,
        [
            status,
            len(rows),
            sum(int(row["row_count"]) for row in rows),
            int(employment_views["legacy"]["rows"]),
            int(employment_views["standalone"]["rows"]),
            int(employment_views["standalone"]["rows"]),
            int(school_year_core["row_count"]),
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
        replace_build_summary(
            connection,
            selected_rows,
            employment_views,
            school_year_core,
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
