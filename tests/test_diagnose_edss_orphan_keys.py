import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import diagnose_edss_orphan_keys as diagnosis


def write_panel(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["_panel_year", "개방ID", "학교구분명", "본분교명"])
        writer.writerows(rows)


class DiagnoseEdssOrphanKeysTests(unittest.TestCase):
    def test_classifies_and_deduplicates_orphans_across_datasets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = root / "base.csv.gz"
            first = root / "first.csv.gz"
            second = root / "second.csv.gz"
            write_panel(base, [("2010", "A", "대학", "본교"), ("2012", "A", "대학", "본교"), ("2011", "B", "대학", "본교")])
            write_panel(first, [("2009", "A", "대학", ""), ("2011", "A", "대학", ""), ("2012", "B", "대학", ""), ("2011", "C", "대학", "")])
            write_panel(second, [("2011", "A", "대학", ""), ("2011", "C", "대학", ""), ("2011", "C", "대학", "")])
            catalog = root / "catalog.csv"
            with catalog.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["catalog_code", "dataset", "access_tier", "output_path"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"catalog_code": "0101", "dataset": "base", "access_tier": "panel", "output_path": base},
                        {"catalog_code": "0305", "dataset": "first", "access_tier": "panel", "output_path": first},
                        {"catalog_code": "0310", "dataset": "second", "access_tier": "panel", "output_path": second},
                    ]
                )

            summary, records = diagnosis.diagnose(catalog)
            record_by_key = {(row["year"], row["open_id"]): row for row in records}
            self.assertEqual(summary["dataset_orphan_key_occurrence_count"], 6)
            self.assertEqual(summary["distinct_orphan_school_year_key_count"], 4)
            self.assertEqual(record_by_key[("2009", "A")]["classification"], "before_base_first_seen")
            self.assertEqual(record_by_key[("2011", "A")]["classification"], "internal_base_gap")
            self.assertEqual(record_by_key[("2012", "B")]["classification"], "after_base_last_seen")
            self.assertEqual(record_by_key[("2011", "C")]["classification"], "open_id_absent_all_years")
            self.assertEqual(record_by_key[("2011", "C")]["dataset_count"], 2)
            self.assertEqual(record_by_key[("2011", "C")]["affected_row_count"], 3)

    def test_reports_repeated_base_keys_without_treating_them_as_orphans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = root / "base.csv.gz"
            other = root / "other.csv.gz"
            write_panel(base, [("2010", "A", "대학", "본교"), ("2010", "A", "대학", "제2캠퍼스")])
            write_panel(other, [("2010", "A", "대학", "")])
            catalog = root / "catalog.csv"
            with catalog.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["catalog_code", "dataset", "access_tier", "output_path"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"catalog_code": "0101", "dataset": "base", "access_tier": "panel", "output_path": base},
                        {"catalog_code": "0305", "dataset": "other", "access_tier": "panel", "output_path": other},
                    ]
                )
            summary, records = diagnosis.diagnose(catalog)
            self.assertEqual(records, [])
            self.assertEqual(summary["base_repeated_school_year_key_count"], 1)
            self.assertEqual(summary["base_duplicate_extra_row_count"], 1)
            self.assertEqual(summary["base_max_key_multiplicity"], 2)
            self.assertEqual(summary["base_natural_key"], ["_panel_year", "개방ID", "본분교명"])
            self.assertEqual(summary["base_repeated_natural_key_count"], 0)


if __name__ == "__main__":
    unittest.main()
