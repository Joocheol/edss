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

    def test_school_year_core_mart_aggregates_campuses_without_expansion(self):
        duckdb = builder.require_duckdb()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bridge_path = root / "bridge.csv"
            bridge_fields = [
                "_panel_year",
                "개방ID",
                "_0101_exists",
                "_0101_match_status",
                "_review_status",
                "_0101_source_row_count",
                "_0101_branch_count",
                "_0101_branch_names",
                "_0101_province_count",
                "_0101_provinces",
                "_0101_region_count",
                "_0101_regions",
                "_0101_school_type_count",
                "_0101_school_types",
                "_0101_campus_scope",
                "_source_dataset_count",
                "_source_catalog_codes",
                "_source_row_count",
            ]
            with bridge_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=bridge_fields)
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "_panel_year": "2010",
                            "개방ID": "A",
                            "_0101_exists": "true",
                            "_0101_match_status": "matched",
                            "_review_status": "not_required",
                            "_0101_source_row_count": "2",
                            "_0101_branch_count": "2",
                            "_0101_branch_names": "본교|분교",
                            "_0101_province_count": "1",
                            "_0101_provinces": "서울",
                            "_0101_region_count": "1",
                            "_0101_regions": "서울 종로구",
                            "_0101_school_type_count": "1",
                            "_0101_school_types": "대학",
                            "_0101_campus_scope": "multiple_campuses",
                            "_source_dataset_count": "2",
                            "_source_catalog_codes": "고등교육통계:0101|대학정보공시:0101",
                            "_source_row_count": "5",
                        },
                        {
                            "_panel_year": "2011",
                            "개방ID": "X",
                            "_0101_exists": "false",
                            "_0101_match_status": "internal_base_gap",
                            "_review_status": "external_crosscheck_required_internal_gap",
                            "_0101_source_row_count": "0",
                            "_0101_branch_count": "0",
                            "_0101_branch_names": "",
                            "_0101_province_count": "0",
                            "_0101_provinces": "",
                            "_0101_region_count": "0",
                            "_0101_regions": "",
                            "_0101_school_type_count": "0",
                            "_0101_school_types": "",
                            "_0101_campus_scope": "not_observed",
                            "_source_dataset_count": "1",
                            "_source_catalog_codes": "대학정보공시:1209",
                            "_source_row_count": "1",
                        },
                        {
                            "_panel_year": "2022",
                            "개방ID": "B",
                            "_0101_exists": "true",
                            "_0101_match_status": "matched",
                            "_review_status": "not_required",
                            "_0101_source_row_count": "1",
                            "_0101_branch_count": "1",
                            "_0101_branch_names": "본교",
                            "_0101_province_count": "1",
                            "_0101_provinces": "부산",
                            "_0101_region_count": "1",
                            "_0101_regions": "부산 남구",
                            "_0101_school_type_count": "1",
                            "_0101_school_types": "대학",
                            "_0101_campus_scope": "single_campus",
                            "_source_dataset_count": "1",
                            "_source_catalog_codes": "고등교육통계:0101",
                            "_source_row_count": "1",
                        },
                    ]
                )
            bridge_summary_path = root / "bridge_summary.json"
            bridge_summary_path.write_text(
                json.dumps(
                    {
                        "bridge_validation": {
                            "row_count": 3,
                            "unique_key": True,
                        }
                    }
                ),
                encoding="utf-8",
            )

            connection = duckdb.connect(":memory:")
            builder.initialize_database(connection, root / "tmp", "1GB", 1)
            source_fields = [
                "_panel_year",
                "개방ID",
                *(source for source, _output in builder.SCHOOL_YEAR_CORE_METRICS),
            ]
            column_sql = ", ".join(
                f'{builder.quote_identifier(field)} VARCHAR' for field in source_fields
            )
            connection.execute(
                f"CREATE TABLE higher_education.panel_0101 ({column_sql})"
            )
            placeholders = ", ".join("?" for _ in source_fields)
            connection.executemany(
                f"INSERT INTO higher_education.panel_0101 VALUES ({placeholders})",
                [
                    ("2010", "A", *(["1"] * len(builder.SCHOOL_YEAR_CORE_METRICS))),
                    ("2010", "A", *(["2"] * len(builder.SCHOOL_YEAR_CORE_METRICS))),
                    ("2022", "B", *(["4"] * len(builder.SCHOOL_YEAR_CORE_METRICS))),
                ],
            )

            result = builder.build_school_year_core_mart(
                connection,
                bridge_path,
                bridge_summary_path,
                Path(__file__).resolve().parents[1]
                / "data/metadata/edss_school_year_core_data_dictionary.csv",
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["row_count"], 3)
            self.assertEqual(result["distinct_key_count"], 3)
            self.assertEqual(result["duplicate_key_count"], 0)
            self.assertEqual(result["matched_0101_row_count"], 2)
            self.assertEqual(result["unmatched_0101_row_count"], 1)
            self.assertEqual(result["multiple_campus_row_count"], 1)
            self.assertEqual(result["source_0101_row_count"], 3)
            self.assertEqual(result["aggregated_0101_key_count"], 2)
            self.assertEqual(result["accounted_0101_source_row_count"], 3)
            self.assertEqual(result["join_expansion_count"], 0)
            values = connection.execute(
                """
                SELECT 개방ID, enrolled_student_count
                FROM analysis.school_year_core_2010_2022
                ORDER BY _panel_year
                """
            ).fetchall()
            self.assertEqual(values, [("A", 3), ("X", None), ("B", 4)])
            connection.close()

    def test_employment_school_year_mart_aggregates_before_core_join(self):
        duckdb = builder.require_duckdb()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            connection = duckdb.connect(":memory:")
            builder.initialize_database(connection, root / "tmp", "1GB", 1)
            source_fields = [
                "_panel_year",
                "개방ID",
                *(source for source, _output in builder.EMPLOYMENT_SCHOOL_YEAR_METRICS),
            ]
            column_sql = ", ".join(
                f'{builder.quote_identifier(field)} VARCHAR' for field in source_fields
            )
            connection.execute(
                f"CREATE TABLE analysis.employment_legacy_2010_2022 ({column_sql})"
            )
            placeholders = ", ".join("?" for _ in source_fields)
            source_rows = []
            core_rows = []
            for year in range(2010, 2023):
                year_text = str(year)
                further_study = "0" if 2016 <= year <= 2019 else "1"
                metrics = ["1", "1", "1", "0", further_study, *("0" for _ in range(6))]
                source_rows.append((year_text, "A", *metrics))
                core_rows.append((year_text, "A"))
            connection.executemany(
                f"INSERT INTO analysis.employment_legacy_2010_2022 "
                f"VALUES ({placeholders})",
                source_rows,
            )
            core_rows.append(("2010", "X"))
            connection.execute(
                "CREATE TABLE analysis.school_year_core_2010_2022 "
                "(_panel_year VARCHAR, 개방ID VARCHAR)"
            )
            connection.executemany(
                "INSERT INTO analysis.school_year_core_2010_2022 VALUES (?, ?)",
                core_rows,
            )

            result = builder.build_employment_school_year_mart(
                connection,
                Path(__file__).resolve().parents[1]
                / "data/metadata/edss_employment_school_year_data_dictionary.csv",
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["row_count"], 13)
            self.assertEqual(result["distinct_key_count"], 13)
            self.assertEqual(result["source_row_count"], 13)
            self.assertEqual(result["accounted_source_row_count"], 13)
            self.assertEqual(result["orphan_employment_key_count"], 0)
            self.assertEqual(result["joined_core_row_count"], 14)
            self.assertEqual(result["joined_core_employment_matched_count"], 13)
            self.assertEqual(result["joined_core_employment_unmatched_count"], 1)
            self.assertEqual(result["join_expansion_count"], 0)
            finding = result["quality_findings"]
            self.assertTrue(
                finding["duplicate_year_comparison"]["exact_duplicate_detected"]
            )
            self.assertEqual(
                finding["all_zero_reported_further_study_years"],
                ["2016", "2017", "2018", "2019"],
            )
            joined = connection.execute(
                """
                SELECT _panel_year, 개방ID, _employment_exists,
                       employment_time_comparison_eligible,
                       employment_reported_employed_count
                FROM analysis.school_year_core_with_employment_2010_2022
                WHERE (_panel_year = '2022' AND 개방ID = 'A')
                   OR (_panel_year = '2010' AND 개방ID = 'X')
                ORDER BY _panel_year DESC, 개방ID
                """
            ).fetchall()
            self.assertEqual(
                joined,
                [
                    ("2022", "A", "true", False, 1),
                    ("2010", "X", "false", None, None),
                ],
            )
            connection.close()


if __name__ == "__main__":
    unittest.main()
