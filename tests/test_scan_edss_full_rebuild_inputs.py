import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import scan_edss_full_rebuild_inputs as scanner


class ScanEdssFullRebuildInputsTests(unittest.TestCase):
    def test_advertised_years_preserves_discontinuous_ranges(self):
        self.assertEqual(
            scanner.advertised_years("2009~2019, 2021~2022"),
            {str(year) for year in range(2009, 2020)} | {"2021", "2022"},
        )

    def test_scans_header_variants_and_records_year_scope_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "raw/table.zip"
            archive.parent.mkdir(parents=True)
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
                handle.writestr("자료(20).csv", "조사년도,개방ID,값\n2020,0001,1\n".encode("cp949"))
                handle.writestr("자료(21).csv", "조사년도,개방ID,새값\n2021,0001,2\n".encode("cp949"))
            entries = [
                {
                    "source": "고등교육통계",
                    "catalog_code": "0101",
                    "dataset": "학교",
                    "domn_code": "1",
                    "advertised_years": "2020~2022",
                    "_archive_paths": [archive.as_posix()],
                    "_inventory_archive_paths": ["raw/table.zip"],
                }
            ]

            profiles, summary = scanner.scan_entries(entries, root / "raw", root, progress_every=0)

            self.assertEqual(profiles[0]["header_variant_count"], 2)
            self.assertEqual(profiles[0]["missing_advertised_years"], ["2022"])
            self.assertTrue(profiles[0]["has_open_id"])
            self.assertEqual(summary["logical_tables_with_schema_variants"], 1)
            self.assertEqual(summary["verification"]["issue_counts_by_type"]["advertised_observed_year_difference"], 1)
            self.assertTrue(summary["verification"]["panel_build_ready"])

    def test_malformed_rows_block_panel_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "raw/bad.zip"
            archive.parent.mkdir(parents=True)
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
                handle.writestr("자료_2024.csv", "조사년도,개방ID\n2024,0001,extra\n".encode("cp949"))
            entries = [
                {
                    "source": "대학정보공시",
                    "catalog_code": "0201",
                    "dataset": "재정",
                    "domn_code": "2",
                    "advertised_years": "2024",
                    "_archive_paths": [archive.as_posix()],
                    "_inventory_archive_paths": ["raw/bad.zip"],
                }
            ]

            profiles, summary = scanner.scan_entries(entries, root / "raw", root, progress_every=0)

            self.assertEqual(profiles[0]["malformed_row_count"], 1)
            self.assertEqual(summary["verification"]["issue_counts_by_severity"]["critical"], 1)
            self.assertFalse(summary["verification"]["panel_build_ready"])

    def test_scan_exception_keeps_physical_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "raw/missing.zip"
            entries = [
                {
                    "source": "취업통계",
                    "catalog_code": "0001",
                    "dataset": "학생인적취업정보",
                    "domn_code": "9",
                    "advertised_years": "2024",
                    "_archive_paths": [missing.as_posix()],
                    "_inventory_archive_paths": ["raw/missing.zip"],
                }
            ]

            profiles, summary = scanner.scan_entries(entries, root / "raw", root, progress_every=0)

            self.assertEqual(profiles, [])
            issue = summary["issues"][0]
            self.assertEqual(issue["type"], "scan_exception")
            self.assertEqual(issue["dataset"], "학생인적취업정보")
            self.assertEqual(issue["archive_paths"], ["raw/missing.zip"])
            self.assertFalse(summary["verification"]["scan_complete"])


if __name__ == "__main__":
    unittest.main()
