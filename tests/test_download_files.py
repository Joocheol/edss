import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import download_files


class DownloadFileTests(unittest.TestCase):
    def test_safe_component_removes_path_separators(self):
        self.assertEqual(download_files.safe_component("a/b:c", "fallback"), "a_b_c")

    def test_manifest_reader_uses_latest_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.jsonl"
            path.write_text('{"dataset_id":"1","sha256":"old"}\n{"dataset_id":"1","sha256":"new"}\n', encoding="utf-8")
            self.assertEqual(download_files.latest_manifest(path)["1"]["sha256"], "new")

    def test_html_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.csv"
            path.write_text("<html>captcha</html>", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                download_files.validate_payload(path, "csv")

    def test_xlsx_signature_is_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.xlsx"
            path.write_bytes(b"not a workbook")
            with self.assertRaises(RuntimeError):
                download_files.validate_payload(path, "xlsx")


if __name__ == "__main__":
    unittest.main()
