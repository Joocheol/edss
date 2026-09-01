import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_edss_full_rebuild_inventory as builder


class BuildEdssFullRebuildInventoryTests(unittest.TestCase):
    def test_reconciles_full_inputs_and_current_pipeline_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "raw/first.zip"
            second = root / "raw/second.zip"
            third = root / "raw/third.zip"
            first.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            third.write_bytes(b"third")

            targets = [
                {"source": "고등교육통계", "dataset": "학교", "domn_code": "1", "major_area": "통합", "advertised_years": "2009~2013"},
                {"source": "고등교육통계", "dataset": "학교", "domn_code": "2", "major_area": "통합", "advertised_years": "2014~2025"},
                {"source": "대학정보공시", "dataset": "재정", "domn_code": "3", "major_area": "재정", "advertised_years": "2025"},
            ]
            records = [
                self.archive_record("1", "0101", "학교", "고등교육통계", first, root),
                self.archive_record("2", "0101", "학교", "고등교육통계", second, root),
                self.archive_record("3", "0201", "재정", "대학정보공시", third, root),
            ]
            statuses = [
                {"domn_code": record["domn_code"], "status": "downloaded", "archive_count": "1", "size_bytes": str(record["size_bytes"])}
                for record in records
            ]
            current = [{"source": "고등교육통계", "catalog_code": "0101", "dataset": "학교", "domn_code": "1"}]

            rows, summary = builder.build_inventory(
                targets,
                statuses,
                records[:1],
                records[1:],
                current,
                root,
                verify_sha256=True,
            )

            self.assertEqual(len(rows), 3)
            self.assertEqual(summary["logical_table_count"], 2)
            self.assertEqual(summary["physical_unit_count"], 3)
            self.assertEqual(summary["archive_count"], 3)
            self.assertEqual(summary["multi_physical_logical_table_count"], 1)
            self.assertEqual(summary["current_pipeline"]["logical_table_count"], 1)
            self.assertEqual(summary["not_in_current_pipeline"]["physical_unit_count"], 2)
            self.assertEqual(summary["verification"]["verified_sha256_count"], 3)
            self.assertTrue(summary["verification"]["complete"])

    def test_records_missing_archive_and_unresolved_catalog_code(self):
        targets = [{"source": "취업통계", "dataset": "학생", "domn_code": "9", "major_area": "취업", "advertised_years": "2024"}]
        statuses = [{"domn_code": "9", "status": "downloaded", "archive_count": "1", "size_bytes": "10"}]

        rows, summary = builder.build_inventory(targets, statuses, [], [], [], Path("."), verify_files=False)

        self.assertEqual(rows[0]["catalog_code"], "")
        issue_types = {item["type"] for item in summary["issues"]}
        self.assertIn("missing_archive_record", issue_types)
        self.assertIn("catalog_code_not_resolved", issue_types)
        self.assertIn("status_archive_count_mismatch", issue_types)
        self.assertFalse(summary["verification"]["complete"])

    @staticmethod
    def archive_record(domn_code, catalog_code, dataset, source, path, root):
        return {
            "domn_code": domn_code,
            "catalog_code": catalog_code,
            "dataset": dataset,
            "source": source,
            "status": "downloaded",
            "file_year": "ALL",
            "local_path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }


if __name__ == "__main__":
    unittest.main()
