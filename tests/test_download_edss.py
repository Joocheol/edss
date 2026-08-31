import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import download_edss


class DownloadEdssTests(unittest.TestCase):
    def test_clean_preserves_korean_and_removes_separators(self):
        self.assertEqual(download_edss.clean("대학/학과:현황"), "대학_학과_현황")

    def test_parse_filename_utf8(self):
        headers = Message()
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''%EB%8C%80%ED%95%99.zip"
        self.assertEqual(download_edss.parse_filename(headers, "fallback.bin"), "대학.zip")

    def test_existing_records_uses_only_successes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.jsonl"
            rows = [
                {"domn_code": "1", "file_year": "ALL", "status": "downloaded"},
                {"domn_code": "2", "file_year": "ALL", "status": "download_failed"},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            self.assertEqual(set(download_edss.existing_records(path)), {("1", "ALL")})


if __name__ == "__main__":
    unittest.main()
