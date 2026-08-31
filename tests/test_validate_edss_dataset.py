import gzip
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_edss_dataset as validator


class ValidateEdssDatasetTests(unittest.TestCase):
    def test_parse_year_ranges_with_gap(self):
        self.assertEqual(
            validator.parse_years("2009~2020, 2022~2025"),
            {str(year) for year in range(2009, 2021)} | {str(year) for year in range(2022, 2026)},
        )

    def test_combines_physical_year_ranges_for_logical_dataset(self):
        config = [
            {"source": "취업통계", "catalog_code": "0001", "dataset": "학생인적취업정보", "advertised_years": "2010~2022"},
            {"source": "취업통계", "catalog_code": "0001", "dataset": "학생인적취업정보", "advertised_years": "2023~2024"},
        ]
        years = validator.expected_years_by_logical_dataset(config)
        self.assertEqual(years[("취업통계", "0001", "학생인적취업정보")], {str(year) for year in range(2010, 2025)})

    def test_profile_counts_candidate_field_absent_from_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "panel.csv.gz"
            with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
                handle.write("_source_row_id,_source_archive_sha256,_panel_year,조사년도,개방ID\n")
                handle.write("r,a,2023,2023,\n")
            result = validator.profile_panel(path, ["개방ID", "학교명"])
            self.assertEqual(result["candidate_identifier_missing_counts"], {"개방ID": 1, "학교명": 1})


if __name__ == "__main__":
    unittest.main()
