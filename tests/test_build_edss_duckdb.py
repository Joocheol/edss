import csv
import gzip
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_edss_duckdb.py"
SPEC = importlib.util.spec_from_file_location("build_edss_duckdb", SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


class BuildEdssDuckDBTests(unittest.TestCase):
    def test_table_names_are_source_scoped(self):
        higher = builder.table_key({"source": "고등교육통계", "catalog_code": "0101"})
        disclosure = builder.table_key({"source": "대학정보공시", "catalog_code": "0101"})
        self.assertEqual(higher, ("higher_education", "panel_0101"))
        self.assertEqual(disclosure, ("university_disclosure", "panel_0101"))

    def test_rejects_unsafe_catalog_code(self):
        with self.assertRaisesRegex(RuntimeError, "unsafe catalog code"):
            builder.table_name("01-01")

    def test_quote_identifier_doubles_quotes(self):
        self.assertEqual(builder.quote_identifier('a"b'), '"a""b"')

    def test_catalog_rejects_duplicate_table_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            panel = root / "panel.csv.gz"
            with gzip.open(panel, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["_panel_year", "개방ID"])
                writer.writerow(["2024", "0000000001"])
            catalog = root / "catalog.csv"
            fields = [
                "source",
                "catalog_code",
                "dataset",
                "access_tier",
                "row_count",
                "column_count",
                "output_path",
                "output_bytes",
                "output_sha256",
            ]
            row = {
                "source": "고등교육통계",
                "catalog_code": "0101",
                "dataset": "테스트",
                "access_tier": "panel",
                "row_count": "1",
                "column_count": "2",
                "output_path": "panel.csv.gz",
                "output_bytes": str(panel.stat().st_size),
                "output_sha256": "0" * 64,
            }
            with catalog.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows([row, row])
            with self.assertRaisesRegex(RuntimeError, "duplicate DuckDB table key"):
                builder.read_catalog(catalog, root)

    def test_employment_analysis_views_enforce_schema_break(self):
        duckdb = builder.require_duckdb()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "standalone.csv.gz"
            with gzip.open(source, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "_panel_year",
                        "학교명",
                        "_open_id_candidate",
                        "_open_id_candidate_method",
                        "_open_id_candidate_status",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "_panel_year": "2023",
                            "학교명": "가대학교",
                            "_open_id_candidate": "A",
                            "_open_id_candidate_method": "audit_only",
                            "_open_id_candidate_status": "candidate",
                        },
                        {
                            "_panel_year": "2024",
                            "학교명": "나대학교",
                            "_open_id_candidate": "B",
                            "_open_id_candidate_method": "audit_only",
                            "_open_id_candidate_status": "candidate",
                        },
                    ]
                )
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            summary_path = root / "resolution.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "status": "complete_with_scope_exclusion",
                        "employment": {"legacy_panel_eligible_row_count": 0},
                        "outputs": {
                            "derived_employment": {
                                "row_count": 2,
                                "sha256": source_sha256,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            connection = duckdb.connect(":memory:")
            builder.initialize_database(connection, root / "tmp", "1GB", 1)
            connection.execute(
                "CREATE TABLE employment.panel_0001 (_panel_year VARCHAR, 개방ID VARCHAR)"
            )
            connection.executemany(
                "INSERT INTO employment.panel_0001 VALUES (?, ?)",
                [("2010", "A"), ("2022", "B"), ("2023", ""), ("2024", "")],
            )
            connection.execute(
                "CREATE TABLE employment.safe_2023_2024_resolved (개방ID VARCHAR)"
            )
            connection.execute(
                "CREATE VIEW analysis.employment_2023_2024_resolved AS "
                "SELECT * FROM employment.safe_2023_2024_resolved"
            )

            result = builder.load_employment_analysis_views(
                connection,
                source,
                summary_path,
            )

            self.assertEqual(result["status"], "complete_with_scope_exclusion")
            self.assertEqual(result["legacy"]["rows"], 2)
            self.assertEqual(result["legacy"]["first_year"], "2010")
            self.assertEqual(result["legacy"]["last_year"], "2022")
            self.assertEqual(result["legacy"]["scope_excluded_year_rows"], 0)
            self.assertEqual(result["legacy"]["missing_open_id_rows"], 0)
            self.assertEqual(result["standalone"]["rows"], 2)
            self.assertFalse(result["standalone"]["canonical_open_id_column_present"])
            self.assertFalse(result["standalone"]["candidate_open_id_column_present"])
            standalone_fields = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'analysis'
                      AND table_name = 'employment_2023_2024_standalone'
                    """
                ).fetchall()
            }
            self.assertNotIn("개방ID", standalone_fields)
            self.assertNotIn("_open_id_candidate", standalone_fields)
            self.assertFalse(
                builder.relation_exists(
                    connection,
                    "employment",
                    "safe_2023_2024_resolved",
                )
            )
            old_view_count = connection.execute(
                """
                SELECT count(*)
                FROM information_schema.views
                WHERE table_schema = 'analysis'
                  AND table_name = 'employment_2023_2024_resolved'
                """
            ).fetchone()[0]
            self.assertEqual(old_view_count, 0)
            connection.close()


if __name__ == "__main__":
    unittest.main()
