#!/usr/bin/env python3
"""Build a resumable DuckDB warehouse from every cataloged EDSS panel.

Each logical panel is stored as a separate table so incompatible grains and
schemas are never flattened into one relation.  The full database is restricted
because it contains the historical employment panel.  A privacy-safe resolved
2023–2024 employment table is retained only for standalone reference analysis;
it is excluded from legacy OpenID longitudinal integration.
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
DEFAULT_RESOLVED_EMPLOYMENT = Path(
    "data/processed/edss/derived/employment_2023_2024_school_department_resolved.csv.gz"
)
DEFAULT_APPLICATION_AUDIT = Path("data/metadata/edss_employment_open_id_application.json")


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


def load_resolved_employment(connection, source_path: Path, application_audit_path: Path) -> dict[str, object]:
    if not source_path.is_file():
        raise RuntimeError(f"resolved employment panel is missing: {source_path}")
    audit = json.loads(application_audit_path.read_text(encoding="utf-8"))
    expected_sha256 = audit["output"]["sha256"]
    actual_sha256 = sha256_file(source_path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError("resolved employment checksum does not match application audit")

    schema_name = "employment"
    relation_name = "safe_2023_2024_resolved"
    temporary_name = "__loading_safe_2023_2024_resolved"
    qualified = f"{quote_identifier(schema_name)}.{quote_identifier(relation_name)}"
    temporary_qualified = f"{quote_identifier(schema_name)}.{quote_identifier(temporary_name)}"
    connection.execute(f"DROP TABLE IF EXISTS {temporary_qualified}")
    connection.execute(
        f"""
        CREATE TABLE {temporary_qualified} AS
        SELECT * FROM read_csv(
            ?, header = true, all_varchar = true, compression = 'gzip',
            encoding = 'utf-8', nullstr = '{NULL_SENTINEL}', strict_mode = true
        )
        """,
        [str(source_path)],
    )
    rows, columns = relation_dimensions(connection, schema_name, temporary_name)
    expected_rows = int(audit["output"]["row_count"])
    expected_columns = int(audit["output"]["column_count"])
    if (rows, columns) != (expected_rows, expected_columns):
        connection.execute(f"DROP TABLE {temporary_qualified}")
        raise RuntimeError("resolved employment dimensions do not match application audit")
    applied_rows = int(
        connection.execute(
            f"SELECT count(*) FROM {temporary_qualified} WHERE coalesce(개방ID, '') <> ''"
        ).fetchone()[0]
    )
    expected_applied = int(audit["application"]["applied_row_count"])
    if applied_rows != expected_applied:
        connection.execute(f"DROP TABLE {temporary_qualified}")
        raise RuntimeError("resolved employment OpenID coverage does not match application audit")

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(f"DROP VIEW IF EXISTS analysis.employment_2023_2024_resolved")
        connection.execute(f"DROP TABLE IF EXISTS {qualified}")
        connection.execute(
            f"ALTER TABLE {temporary_qualified} RENAME TO {quote_identifier(relation_name)}"
        )
        connection.execute(
            f"CREATE VIEW analysis.employment_2023_2024_resolved AS SELECT * FROM {qualified}"
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return {
        "schema": schema_name,
        "table": relation_name,
        "rows": rows,
        "columns": columns,
        "applied_open_id_rows": applied_rows,
        "remaining_missing_open_id_rows": rows - applied_rows,
        "sha256": actual_sha256,
    }


def replace_build_summary(connection, rows: list[dict[str, str]], resolved: dict[str, object], status: str) -> None:
    connection.execute("DROP TABLE IF EXISTS meta.database_summary")
    connection.execute(
        """
        CREATE TABLE meta.database_summary AS
        SELECT
            ?::VARCHAR AS status,
            ?::BIGINT AS panel_table_count,
            ?::BIGINT AS panel_row_count,
            ?::BIGINT AS safe_resolved_employment_rows,
            ?::BIGINT AS safe_resolved_employment_open_id_rows
        """,
        [
            status,
            len(rows),
            sum(int(row["row_count"]) for row in rows),
            int(resolved["rows"]),
            int(resolved["applied_open_id_rows"]),
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
    parser.add_argument("--resolved-employment", type=Path, default=DEFAULT_RESOLVED_EMPLOYMENT)
    parser.add_argument("--application-audit", type=Path, default=DEFAULT_APPLICATION_AUDIT)
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
    resolved_path = (
        args.resolved_employment
        if args.resolved_employment.is_absolute()
        else repo_root / args.resolved_employment
    )
    application_audit_path = (
        args.application_audit
        if args.application_audit.is_absolute()
        else repo_root / args.application_audit
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
        resolved = load_resolved_employment(connection, resolved_path, application_audit_path)
        replace_build_summary(connection, selected_rows, resolved, status)
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
        "resolved_employment": resolved,
        "validation": {
            "catalog_paths_missing": 0,
            "catalog_size_mismatches": 0,
            "panel_dimension_mismatches": 0,
            "blank_strings_preserved_as_empty_strings": True,
            "all_source_columns_loaded_as_varchar": True,
            "incompatible_panel_grains_concatenated": False,
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
