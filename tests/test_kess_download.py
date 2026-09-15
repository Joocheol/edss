import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from kess.download import CollectionError, build_tasks, encode_multipart, filter_tasks, parse_catalog


HTML = """
<ul>
  <li><span>학교별(상반기):</span><span>
    <span onclick="downLoad('240','remote-2026.xlsx','2026년 고등 학교별.xlsx','02');">2026</span>
    <span onclick="downLoad('224','remote-2025.xlsx','2025년 고등 학교별.xlsx','02');">2025</span>
  </span></li>
  <li><span>학교별:</span><span>
    <span onclick="downLoad('120','job-2024.xlsx','24년 학교별 취업통계.xlsx','03');">2024</span>
  </span></li>
</ul>
"""


class KessDownloadTests(unittest.TestCase):
    def test_parse_catalog_preserves_download_identifiers(self):
        rows = parse_catalog(HTML)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].label, "학교별(상반기)")
        self.assertEqual(rows[0].year, 2026)
        self.assertEqual(rows[0].remote_name, "remote-2026.xlsx")
        self.assertEqual(rows[-1].group, "03")
        self.assertEqual(rows[-1].year, 2024)

    def test_build_tasks_validates_each_series_count(self):
        groups = {
            "02": {
                "domain": "고등교육통계",
                "series": {"학교별(상반기)": {"id": "school_spring", "expected_files": 2}},
            },
            "03": {
                "domain": "졸업자취업통계",
                "series": {"학교별": {"id": "school", "expected_files": 1}},
            },
        }
        tasks = build_tasks(parse_catalog(HTML), groups)
        self.assertEqual(len(tasks), 3)
        self.assertEqual({task.series for task in tasks}, {"school_spring", "school"})
        self.assertTrue(all(task.task_id.startswith("kess-") for task in tasks))

    def test_multipart_contains_usage_codes(self):
        body, content_type = encode_multipart(
            {"TYPE_A": "12", "TYPE_B": "02", "FILE_ID": "240", "GROUP_A": "02"}
        )
        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn(b'name="TYPE_A"', body)
        self.assertIn(b"12", body)
        self.assertTrue(body.endswith(b"--\r\n"))

    def test_filter_tasks_rejects_unknown_and_empty_combinations(self):
        groups = {
            "02": {
                "domain": "고등교육통계",
                "series": {"학교별(상반기)": {"id": "school_spring", "expected_files": 2}},
            },
            "03": {
                "domain": "졸업자취업통계",
                "series": {"학교별": {"id": "school", "expected_files": 1}},
            },
        }
        tasks = build_tasks(parse_catalog(HTML), groups)
        self.assertEqual(len(filter_tasks(tasks, years={2026})), 1)
        with self.assertRaises(CollectionError):
            filter_tasks(tasks, series={"typo"})
        with self.assertRaises(CollectionError):
            filter_tasks(tasks, domains={"졸업자취업통계"}, years={2026})


if __name__ == "__main__":
    unittest.main()
