import csv
import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_edss_dataset as builder


class BuildEdssDatasetTests(unittest.TestCase):
    def test_disambiguates_duplicate_headers(self):
        self.assertEqual(builder.disambiguate_header(["개방ID", "개방ID"]), ["개방ID", "개방ID__duplicate_2"])

    def test_infers_short_and_long_years(self):
        self.assertEqual(builder.infer_year("자료(09).csv"), "2009")
        self.assertEqual(builder.infer_year("자료_2025.csv"), "2025")

    def test_archive_year_distinguishes_single_year_from_range(self):
        self.assertEqual(builder.archive_file_year(Path("자료_2010.zip")), "2010")
        self.assertEqual(builder.archive_file_year(Path("자료_2009-2025.zip")), "ALL")

    def test_detect_encoding_accepts_truncated_multibyte_boundary(self):
        encoded = "조사년도".encode("cp949")
        self.assertEqual(builder.detect_encoding(encoded[:-1]), "cp949")

    def test_loads_full_rebuild_inventory_with_explicit_archive_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inventory = root / "inventory.csv"
            archive = root / "data/raw/001.zip"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"zip")
            with inventory.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["source", "catalog_code", "dataset", "domn_code", "advertised_years", "archive_count", "archive_paths", "archive_sha256s"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "source": "고등교육통계",
                        "catalog_code": "0101",
                        "dataset": "학교",
                        "domn_code": "16918",
                        "advertised_years": "2009~2025",
                        "archive_count": "1",
                        "archive_paths": json.dumps(["data/raw/001.zip"]),
                        "archive_sha256s": json.dumps(["0" * 64]),
                    }
                )

            entries = builder.load_rebuild_inventory(inventory, root)

            self.assertEqual(entries[0]["catalog_code"], "0101")
            self.assertEqual(builder.discover_archives(Path("unused"), entries[0]), [archive])

    def test_rebuild_inventory_rejects_archive_count_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inventory = root / "inventory.csv"
            inventory.write_text(
                "source,catalog_code,dataset,domn_code,advertised_years,archive_count,archive_paths,archive_sha256s\n"
                '취업통계,0001,학생,9,2024,2,"[""raw/a.zip""]","[""0000000000000000000000000000000000000000000000000000000000000000""]"\n',
                encoding="utf-8-sig",
            )
            with self.assertRaisesRegex(RuntimeError, "archive_count"):
                builder.load_rebuild_inventory(inventory, root)

    def test_partitioned_digest_counter_counts_extra_occurrences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            counter = builder.PartitionedDigestCounter(Path(temp_dir))
            first = hashlib.sha256(b"first").digest()
            second = hashlib.sha256(b"second").digest()
            for digest in (first, second, first, first):
                counter.add(digest)
            self.assertEqual(counter.duplicate_count(), 2)

    def test_load_scan_profiles_reconciles_inventory_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles_path = root / "profiles.jsonl"
            checksum = "a" * 64
            entry = {
                "source": "고등교육통계",
                "catalog_code": "0101",
                "dataset": "학교",
                "domn_code": "1",
                "advertised_years": "2020",
                "_inventory_archive_paths": ["data/raw/table.zip"],
                "_inventory_archive_sha256s": [checksum],
            }
            profile = {
                "source": "고등교육통계",
                "catalog_code": "0101",
                "dataset": "학교",
                "domn_code": "1",
                "advertised_years": "2020",
                "archive_records": [{"local_path": "data/raw/table.zip", "sha256": checksum}],
            }
            profiles_path.write_text(json.dumps(profile, ensure_ascii=False) + "\n", encoding="utf-8")

            loaded = builder.load_scan_profiles(profiles_path, [entry])

            self.assertEqual(loaded["1"]["dataset"], "학교")

    def test_nested_zip_is_built_as_text_preserving_panel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            raw_dir = root / "raw" / "취업통계" / "0001_학생인적취업정보_13299"
            raw_dir.mkdir(parents=True)
            csv_bytes = "조사년도,개방ID,값\n2010,0000149,01\n".encode("cp949")
            inner_buffer = io.BytesIO()
            with zipfile.ZipFile(inner_buffer, "w", zipfile.ZIP_DEFLATED) as inner:
                inner.writestr("자료(10).csv", csv_bytes)
            archive_path = raw_dir / "0001_학생인적취업정보_2010.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as outer:
                outer.writestr("자료(10).zip", inner_buffer.getvalue())

            entry = {
                "source": "취업통계",
                "catalog_code": "0001",
                "dataset": "학생인적취업정보",
                "domn_code": "13299",
                "advertised_years": "2010",
                "license": "test",
            }
            physical = builder.scan_physical_entry(entry, [archive_path])
            profile, _ = builder.build_logical_panel([(entry, physical)], root / "processed", force=True)
            self.assertEqual(profile["row_count"], 1)
            with gzip.open(profile["output_path"], "rt", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["개방ID"], "0000149")
            self.assertEqual(rows[0]["값"], "01")
            self.assertEqual(rows[0]["_panel_year"], "2010")
            self.assertTrue(rows[0]["_source_row_id"])


if __name__ == "__main__":
    unittest.main()
