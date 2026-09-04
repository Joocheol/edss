import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analyze_edss_employment_balanced_panel.py"
)
SPEC = importlib.util.spec_from_file_location(
    "analyze_edss_employment_balanced_panel", SCRIPT
)
balanced = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(balanced)


def synthetic_aggregates():
    rows = []
    for year in range(2010, 2021):
        cohort = str(year)
        june = year <= 2013
        rows.append(
            {
                "employment_cohort_year": cohort,
                "employment_source_panel_year": cohort if june else str(year + 1),
                "employment_reference_date": (
                    f"{cohort}-06-01" if june else f"{cohort}-12-31"
                ),
                "employment_reference_date_basis": (
                    "june_1" if june else "december_31"
                ),
                "comparison_interval": (
                    "june_1_2010_2013" if june else "december_31_2014_2020"
                ),
                "all_available_school_count": 10 + year % 2,
                "balanced_school_count": 9,
                "all_available_source_record_count": 100,
                "balanced_source_record_count": 90,
                "all_available_reported_graduate_count": 100,
                "balanced_reported_graduate_count": 90,
                "all_available_reported_employed_count": 50 + year - 2010,
                "balanced_reported_employed_count": 45 + year - 2010,
            }
        )
    return rows


def synthetic_composition():
    return {
        str(year): {
            "schools_added_from_previous_comparable_cohort": (
                None if year in (2010, 2014) else 1
            ),
            "schools_missing_from_previous_comparable_cohort": (
                None if year in (2010, 2014) else 1
            ),
        }
        for year in range(2010, 2021)
    }


class AnalyzeEdssEmploymentBalancedPanelTests(unittest.TestCase):
    def test_balanced_subset_and_coverage(self):
        rows = balanced.build_sensitivity_rows(
            synthetic_aggregates(), synthetic_composition()
        )
        by_year = {row["employment_cohort_year"]: row for row in rows}

        self.assertEqual(by_year["2010"]["balanced_school_coverage_share"], 0.9)
        self.assertEqual(by_year["2010"]["balanced_graduate_coverage_share"], 0.9)
        self.assertEqual(
            by_year["2010"]["all_available_reported_employed_share_of_graduates"],
            0.5,
        )
        self.assertEqual(
            by_year["2010"]["balanced_reported_employed_share_of_graduates"],
            0.5,
        )

    def test_reference_date_break_resets_change(self):
        rows = balanced.build_sensitivity_rows(
            synthetic_aggregates(), synthetic_composition()
        )
        by_year = {row["employment_cohort_year"]: row for row in rows}

        self.assertIsNone(by_year["2010"]["previous_comparable_cohort_year"])
        self.assertEqual(by_year["2011"]["previous_comparable_cohort_year"], "2010")
        self.assertIsNone(by_year["2014"]["previous_comparable_cohort_year"])
        self.assertIsNone(
            by_year["2014"][
                "balanced_share_change_pp_from_previous_comparable_cohort"
            ]
        )
        self.assertEqual(by_year["2015"]["previous_comparable_cohort_year"], "2014")

    def test_reports_composition_counts(self):
        rows = balanced.build_sensitivity_rows(
            synthetic_aggregates(), synthetic_composition()
        )
        by_year = {row["employment_cohort_year"]: row for row in rows}

        self.assertIsNone(
            by_year["2010"]["schools_added_from_previous_comparable_cohort"]
        )
        self.assertEqual(
            by_year["2011"]["schools_added_from_previous_comparable_cohort"], 1
        )
        self.assertIsNone(
            by_year["2014"]["schools_missing_from_previous_comparable_cohort"]
        )

    def test_rejects_varying_balanced_membership(self):
        rows = synthetic_aggregates()
        rows[1]["balanced_school_count"] = 8
        with self.assertRaisesRegex(RuntimeError, "varies within interval"):
            balanced.build_sensitivity_rows(rows, synthetic_composition())

    def test_rejects_missing_cohort(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected cohort sequence"):
            balanced.build_sensitivity_rows(
                synthetic_aggregates()[:-1], synthetic_composition()
            )

    def test_source_quality_contract(self):
        quality = {
            "row_count": 5969,
            "blank_open_id_row_count": 0,
            "duplicate_school_cohort_key_count": 0,
            "cohort_count": 11,
            "first_cohort_year": "2010",
            "last_cohort_year": "2020",
        }
        balanced.validate_source_quality(quality)
        quality["duplicate_school_cohort_key_count"] = 1
        with self.assertRaisesRegex(RuntimeError, "unexpected source mart quality"):
            balanced.validate_source_quality(quality)


if __name__ == "__main__":
    unittest.main()
