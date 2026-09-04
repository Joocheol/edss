import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_edss_employment_cohort_trends.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_edss_employment_cohort_trends", SCRIPT
)
trends = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(trends)


def synthetic_aggregates():
    records = []
    for year in range(2010, 2021):
        cohort = str(year)
        june = year <= 2013
        unreliable_further_study = 2015 <= year <= 2018
        records.append(
            {
                "employment_cohort_year": cohort,
                "employment_source_panel_year": (
                    cohort if june else str(year + 1)
                ),
                "employment_reference_date": (
                    f"{cohort}-06-01" if june else f"{cohort}-12-31"
                ),
                "employment_reference_date_basis": (
                    "june_1" if june else "december_31"
                ),
                "employment_comparability_regime": (
                    "june_1_pre_unification"
                    if june
                    else "december_31_post_unification"
                ),
                "school_count": 2,
                "source_record_count": 100,
                "reported_graduate_count": 100,
                "reported_employed_count": 50 + year - 2010,
                "reported_health_insurance_employed_count": 45,
                "reported_school_employed_count": 5,
                "reported_further_study_count": (
                    0 if unreliable_further_study else 10
                ),
                "further_study_quality_status": (
                    "all_zero_source_field"
                    if unreliable_further_study
                    else "as_reported"
                ),
                "reported_excluded_count": 2,
                "reported_other_count": 20,
                "reported_unknown_count": 3,
            }
        )
    return records


class BuildEdssEmploymentCohortTrendsTests(unittest.TestCase):
    def test_break_resets_previous_comparison(self):
        rows = trends.build_trend_rows(synthetic_aggregates())
        by_year = {row["employment_cohort_year"]: row for row in rows}

        self.assertIsNone(by_year["2010"]["previous_comparable_cohort_year"])
        self.assertEqual(by_year["2011"]["previous_comparable_cohort_year"], "2010")
        self.assertIsNone(by_year["2014"]["previous_comparable_cohort_year"])
        self.assertFalse(by_year["2014"]["reference_date_comparable_to_previous"])
        self.assertIsNone(
            by_year["2014"][
                "reported_employed_share_change_pp_from_previous_comparable_cohort"
            ]
        )
        self.assertEqual(by_year["2015"]["previous_comparable_cohort_year"], "2014")

    def test_uses_ratio_of_totals_and_percentage_point_change(self):
        rows = trends.build_trend_rows(synthetic_aggregates())
        by_year = {row["employment_cohort_year"]: row for row in rows}

        self.assertEqual(by_year["2010"]["reported_employed_share_of_graduates"], 0.5)
        self.assertEqual(by_year["2011"]["reported_employed_share_of_graduates"], 0.51)
        self.assertEqual(
            by_year["2011"][
                "reported_employed_share_change_pp_from_previous_comparable_cohort"
            ],
            1.0,
        )

    def test_unreliable_further_study_share_is_null(self):
        rows = trends.build_trend_rows(synthetic_aggregates())
        by_year = {row["employment_cohort_year"]: row for row in rows}

        self.assertEqual(
            by_year["2014"]["reported_further_study_share_of_graduates"], 0.1
        )
        for cohort in ("2015", "2016", "2017", "2018"):
            self.assertIsNone(
                by_year[cohort]["reported_further_study_share_of_graduates"]
            )
        self.assertEqual(
            by_year["2019"]["reported_further_study_share_of_graduates"], 0.1
        )

    def test_rejects_missing_cohort(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected cohort sequence"):
            trends.build_trend_rows(synthetic_aggregates()[:-1])

    def test_rejects_negative_reported_count(self):
        records = synthetic_aggregates()
        records[0]["reported_employed_count"] = -1
        with self.assertRaisesRegex(RuntimeError, "negative reported_employed_count"):
            trends.build_trend_rows(records)


if __name__ == "__main__":
    unittest.main()
