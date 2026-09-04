import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import audit_edss_employment_cohort_years as audit


class EmploymentCohortYearAuditTests(unittest.TestCase):
    def test_row_cohort_year_uses_august_february_cycle(self):
        self.assertEqual(audit.row_cohort_year("202008"), 2021)
        self.assertEqual(audit.row_cohort_year("202102"), 2021)
        self.assertEqual(audit.row_cohort_year("202107"), 2022)
        with self.assertRaises(ValueError):
            audit.row_cohort_year("202113")

    def test_audit_separates_distinct_wave_and_exact_repeat(self):
        duckdb = audit.require_duckdb()
        connection = duckdb.connect(":memory:")
        metric_columns = ", ".join(
            f'"{source}" VARCHAR' for source, _output in audit.METRICS
        )
        connection.execute("CREATE SCHEMA analysis")
        connection.execute(
            "CREATE TABLE analysis.employment_legacy_2010_2022 ("
            "_panel_year VARCHAR, 조사년도 VARCHAR, 개방ID VARCHAR, "
            f"졸업년월 VARCHAR, {metric_columns})"
        )

        rows = []
        for source_year, months, employed_values in (
            ("2014", ("201308", "201402"), ("1", "0")),
            ("2015", ("201308", "201402"), ("1", "1")),
            ("2021", ("201908", "202002"), ("1", "0")),
            ("2022", ("201908", "202002"), ("1", "0")),
        ):
            for open_id, month, employed in zip(
                ("A", "B"), months, employed_values
            ):
                metrics = ["1", employed, employed, "0", "0", "0", "0", "0", "0", "0", "0"]
                rows.append((source_year, source_year, open_id, month, *metrics))
        placeholders = ", ".join("?" for _ in rows[0])
        connection.executemany(
            f"INSERT INTO analysis.employment_legacy_2010_2022 VALUES ({placeholders})",
            rows,
        )

        summary, records = audit.audit_connection(connection)
        connection.close()
        by_year = {record["source_year"]: record for record in records}

        self.assertEqual(by_year["2014"]["inferred_cohort_year"], "2014")
        self.assertEqual(by_year["2015"]["inferred_cohort_year"], "2014")
        self.assertEqual(
            by_year["2014"]["cohort_use_status"],
            "review_repeated_distinct_wave",
        )
        self.assertEqual(
            by_year["2015"]["cohort_use_status"],
            "review_repeated_distinct_wave",
        )
        self.assertEqual(
            by_year["2021"]["cohort_use_status"],
            "eligible_first_of_exact_repeat",
        )
        self.assertEqual(
            by_year["2022"]["cohort_use_status"], "exclude_exact_repeat"
        )
        self.assertEqual(
            by_year["2022"]["official_same_label_status"],
            "conflict_no_official_target_months",
        )
        self.assertEqual(
            by_year["2022"]["same_cohort_exact_school_aggregate_match_count"],
            2,
        )
        self.assertEqual(
            summary["repeated_cohort_groups"],
            {"2014": ["2014", "2015"], "2020": ["2021", "2022"]},
        )
        self.assertEqual(summary["status"], "review_required")


if __name__ == "__main__":
    unittest.main()
