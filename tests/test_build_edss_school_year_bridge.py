import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_edss_school_year_bridge as bridge


FIELDS = ["_panel_year", "개방ID", "학교구분명", "시도명", "지역명", "본분교명"]


def write_panel(path: Path, rows: list[tuple[str, str, str, str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(rows)


def write_catalog(path: Path, panels: list[tuple[str, str, Path]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["catalog_code", "dataset", "access_tier", "output_path"],
        )
        writer.writeheader()
        for code, dataset, panel_path in panels:
            writer.writerow(
                {
                    "catalog_code": code,
                    "dataset": dataset,
                    "access_tier": "panel",
                    "output_path": panel_path,
                }
            )


class BuildEdssSchoolYearBridgeTests(unittest.TestCase):
    def test_aggregates_categorical_attributes_and_preserves_orphans(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = root / "base.csv.gz"
            other = root / "other.csv.gz"
            write_panel(
                base,
                [
                    ("2010", "A", "대학", "서울", "서울 종로구", "본교"),
                    ("2010", "A", "대학", "경기", "경기 수원시", "제2캠퍼스"),
                    ("2012", "A", "대학", "서울", "서울 종로구", "본교"),
                    ("2011", "B", "전문대학", "부산", "부산 남구", "본교"),
                ],
            )
            write_panel(
                other,
                [
                    ("2010", "A", "대학", "", "", ""),
                    ("2010", "A", "대학", "", "", ""),
                    ("2009", "A", "대학", "", "", ""),
                    ("2011", "A", "대학", "", "", ""),
                    ("2012", "B", "전문대학", "", "", ""),
                    ("2011", "C", "대학원", "", "", ""),
                    ("2011", "C", "대학원", "", "", ""),
                    ("2023", "", "대학", "", "", ""),
                ],
            )
            catalog = root / "catalog.csv"
            write_catalog(catalog, [("0101", "base", base), ("0305", "other", other)])

            summary, records = bridge.build_bridge(catalog)
            by_key = {(row["_panel_year"], row["개방ID"]): row for row in records}

            self.assertEqual(len(records), len(by_key))
            self.assertEqual(summary["bridge_validation"]["duplicate_key_count"], 0)
            self.assertEqual(summary["left_join_validation"]["row_expansion_count"], 0)
            self.assertEqual(summary["base_source_row_count"], 4)
            self.assertEqual(summary["base_natural_key_count"], 4)
            self.assertEqual(summary["base_repeated_natural_key_count"], 0)
            self.assertEqual(summary["base_repeated_school_year_key_count"], 1)
            self.assertEqual(summary["source_input_row_count"], 12)
            self.assertEqual(summary["source_missing_join_key_row_count"], 1)

            multi = by_key[("2010", "A")]
            self.assertEqual(multi["_0101_source_row_count"], 2)
            self.assertEqual(multi["_0101_branch_count"], 2)
            self.assertEqual(multi["_0101_branch_names"], "본교|제2캠퍼스")
            self.assertEqual(multi["_0101_provinces"], "경기|서울")
            self.assertEqual(multi["_0101_campus_scope"], "multiple_campuses")
            self.assertEqual(multi["_source_row_count"], 4)

            self.assertEqual(by_key[("2009", "A")]["_0101_match_status"], "before_base_first_seen")
            self.assertEqual(by_key[("2011", "A")]["_0101_match_status"], "internal_base_gap")
            self.assertEqual(
                by_key[("2011", "A")]["_review_status"],
                "external_crosscheck_required_internal_gap",
            )
            self.assertEqual(by_key[("2012", "B")]["_0101_match_status"], "after_base_last_seen")
            self.assertEqual(by_key[("2011", "C")]["_0101_match_status"], "open_id_absent_all_years")
            self.assertEqual(
                by_key[("2011", "C")]["_review_status"],
                "external_crosscheck_required_absent_all_years",
            )
            self.assertEqual(by_key[("2011", "C")]["_0101_source_row_count"], 0)
            self.assertEqual(by_key[("2011", "C")]["_0101_branch_names"], "")

            # A dictionary lookup represents the allowed many-to-one bridge join.
            # Every keyed fact row matches at most one bridge row; blank keys remain.
            fact_rows = list(bridge.panel_rows(other))
            joined_rows = [(row, by_key.get((row[0], row[1]))) for row in fact_rows]
            self.assertEqual(len(joined_rows), len(fact_rows))

    def test_validator_rejects_duplicate_bridge_keys(self):
        row = {
            "_panel_year": "2010",
            "개방ID": "A",
            "_0101_exists": "true",
            "_0101_match_status": "matched",
            "_review_status": "not_required",
            "_0101_source_row_count": 1,
            "_0101_branch_count": 1,
            "_0101_campus_scope": "single_campus",
        }
        with self.assertRaises(ValueError):
            bridge.validate_bridge_records([row, row.copy()])

    def test_builder_rejects_repeated_0101_natural_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = root / "base.csv.gz"
            repeated = ("2010", "A", "대학", "서울", "서울 종로구", "본교")
            write_panel(base, [repeated, repeated])
            catalog = root / "catalog.csv"
            write_catalog(catalog, [("0101", "base", base)])
            with self.assertRaises(RuntimeError):
                bridge.build_bridge(catalog)


if __name__ == "__main__":
    unittest.main()
