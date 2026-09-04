import importlib.util
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analyze_edss_employment_stratified_trends.py"
)
SPEC = importlib.util.spec_from_file_location(
    "analyze_edss_employment_stratified_trends", SCRIPT
)
stratified = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(stratified)


def synthetic_rows():
    rows = []
    for year in range(2010, 2021):
        cohort = str(year)
        june = year <= 2013
        basis = "june_1" if june else "december_31"
        for open_id, school_type, province, province_count in (
            ("001", "대학", "서울", 1),
            ("002", "전문대학", "경기", 1),
            ("003", "대학", "서울|충남", 2),
        ):
            current_province = province
            current_count = province_count
            if open_id == "002" and year == 2017:
                current_province = "인천"
            if open_id == "004":
                current_count = 0
            rows.append(
                {
                    "employment_cohort_year": cohort,
                    "employment_source_panel_year": (
                        cohort if june else str(year + 1)
                    ),
                    "employment_reference_date": (
                        f"{cohort}-06-01" if june else f"{cohort}-12-31"
                    ),
                    "employment_reference_date_basis": basis,
                    "open_id": open_id,
                    "school_types": school_type,
                    "school_type_count": 1,
                    "provinces": current_province,
                    "province_count": current_count,
                    "source_record_count": 10,
                    "reported_graduate_count": 100,
                    "reported_employed_count": 50 + year - 2010,
                }
            )
    return rows


class AnalyzeEdssEmploymentStratifiedTrendsTests(unittest.TestCase):
    def test_attribute_classification_preserves_multiple_and_missing(self):
        row = synthetic_rows()[2]
        self.assertEqual(
            stratified.attribute_value(row, "province"),
            ("복수시도", False, True),
        )
        row["provinces"] = ""
        row["province_count"] = 0
        self.assertEqual(
            stratified.attribute_value(row, "province"),
            ("속성없음", True, False),
        )

    def test_quality_uses_graduate_weighted_coverage(self):
        rows = synthetic_rows()
        rows[0]["school_types"] = ""
        rows[0]["school_type_count"] = 0
        quality = stratified.build_quality_rows(rows)
        row = next(
            value
            for value in quality
            if value["employment_cohort_year"] == "2010"
            and value["attribute"] == "school_type"
        )
        self.assertEqual(row["missing_attribute_school_count"], 1)
        self.assertAlmostEqual(row["attribute_school_coverage_share"], 2 / 3)
        self.assertAlmostEqual(row["attribute_graduate_coverage_share"], 2 / 3)

    def test_stability_detects_province_transition(self):
        stability = stratified.build_stability_rows(synthetic_rows())
        december_province = next(
            row
            for row in stability
            if row["attribute"] == "province"
            and row["comparison_interval"] == "december_31_2014_2020"
        )
        december_school_type = next(
            row
            for row in stability
            if row["attribute"] == "school_type"
            and row["comparison_interval"] == "december_31_2014_2020"
        )
        self.assertEqual(december_province["attribute_transition_count"], 2)
        self.assertEqual(december_province["stable_attribute_balanced_school_count"], 2)
        self.assertEqual(december_school_type["stable_attribute_balanced_school_count"], 3)

    def test_stratum_balanced_requires_same_stratum_every_year(self):
        trends = stratified.build_stratified_rows(synthetic_rows())
        rows = {
            (row["attribute"], row["stratum"], row["employment_cohort_year"]): row
            for row in trends
        }
        self.assertEqual(
            rows[("province", "경기", "2016")]["stratum_balanced_school_count"],
            0,
        )
        self.assertEqual(
            rows[("school_type", "대학", "2016")][
                "stratum_balanced_school_count"
            ],
            2,
        )

    def test_ratio_of_totals_and_interval_break(self):
        rows = synthetic_rows()
        rows[0]["reported_graduate_count"] = 300
        rows[0]["reported_employed_count"] = 90
        trends = stratified.build_stratified_rows(rows)
        indexed = {
            (row["attribute"], row["stratum"], row["employment_cohort_year"]): row
            for row in trends
        }
        university_2010 = indexed[("school_type", "대학", "2010")]
        university_2014 = indexed[("school_type", "대학", "2014")]
        self.assertEqual(
            university_2010[
                "all_available_reported_employed_share_of_graduates"
            ],
            0.35,
        )
        self.assertIsNone(university_2014["previous_comparable_cohort_year"])
        self.assertIsNone(
            university_2014[
                "all_available_share_change_pp_from_previous_comparable_cohort"
            ]
        )

    def test_strata_reconcile_to_source(self):
        source = synthetic_rows()
        trends = stratified.build_stratified_rows(source)
        result = stratified.validate_outputs(source, trends)
        self.assertTrue(result["school_type_strata_reconcile_to_source_by_cohort"])
        self.assertTrue(result["province_strata_reconcile_to_source_by_cohort"])

    def test_source_contract_rejects_duplicate_key(self):
        rows = synthetic_rows()
        stratified.EXPECTED_SOURCE_ROW_COUNT = len(rows)
        try:
            stratified.validate_source_rows(rows)
            rows.append(dict(rows[0]))
            stratified.EXPECTED_SOURCE_ROW_COUNT = len(rows)
            with self.assertRaisesRegex(RuntimeError, "unexpected source mart quality"):
                stratified.validate_source_rows(rows)
        finally:
            stratified.EXPECTED_SOURCE_ROW_COUNT = 5969


if __name__ == "__main__":
    unittest.main()
