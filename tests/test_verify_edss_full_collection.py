import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import verify_edss_full_collection as verifier


class VerifyEdssFullCollectionTests(unittest.TestCase):
    def test_reconciles_downloaded_failed_and_pending_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "data/raw/ok.zip"
            archive_path.parent.mkdir(parents=True)
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("data.csv", "연도,개방ID\n2025,0001\n")
            checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            targets = [
                {"source": "고등교육통계", "domn_code": "1", "dataset": "A", "advertised_years": "2025"},
                {"source": "대학정보공시", "domn_code": "2", "dataset": "B", "advertised_years": "2025"},
                {"source": "대학정보공시", "domn_code": "3", "dataset": "C", "advertised_years": "2025"},
            ]
            existing = [{"domn_code": "1", "status": "downloaded", "local_path": "data/raw/ok.zip", "filename": "ok.zip", "sha256": checksum, "size_bytes": archive_path.stat().st_size}]
            attempts = [{"domn_code": "2", "status": "download_failed", "error_type": "TimeoutError"}]
            rows, summary = verifier.build_status(targets, existing, attempts, root)
            self.assertEqual([row["status"] for row in rows], ["downloaded", "failed", "pending"])
            self.assertEqual(summary["verified_archive_count"], 1)
            self.assertFalse(summary["complete"])

    def test_detects_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("x.csv", "x\n1\n")
            targets = [{"source": "취업통계", "domn_code": "9", "dataset": "X", "advertised_years": "2025"}]
            attempts = [{"domn_code": "9", "status": "downloaded", "local_path": "bad.zip", "filename": "bad.zip", "sha256": "0" * 64, "size_bytes": path.stat().st_size}]
            rows, summary = verifier.build_status(targets, [], attempts, root)
            self.assertEqual(rows[0]["status"], "invalid")
            self.assertIn("sha256_mismatch", rows[0]["errors"])
            self.assertEqual(summary["invalid_archive_count"], 1)


if __name__ == "__main__":
    unittest.main()
