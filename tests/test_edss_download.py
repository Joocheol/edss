import sys
import unittest
import urllib.parse
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from edss.download import CollectionError, EdssDataset, encode_download_form, select_scope, select_task


def dataset(source: str, code: str) -> EdssDataset:
    return EdssDataset(source, "대영역", f"자료-{code}", code, "2009~2025", "조사년도")


class EdssDownloadTests(unittest.TestCase):
    def test_select_scope_checks_expected_counts(self):
        rows = [dataset("고등교육통계", "1"), dataset("대학정보공시", "2")]
        selected = select_scope(rows, {"고등교육통계": 1, "대학정보공시": 1})
        self.assertEqual([row.domn_code for row in selected], ["1", "2"])
        with self.assertRaises(CollectionError):
            select_scope(rows, {"고등교육통계": 2, "대학정보공시": 1})

    def test_domn_filter_requires_every_requested_code(self):
        rows = [dataset("고등교육통계", "1")]
        with self.assertRaises(CollectionError):
            select_scope(
                rows,
                {"고등교육통계": 1},
                domn_codes={"1", "missing"},
            )

    def test_select_task_uses_exact_advertised_file_year(self):
        row = dataset("고등교육통계", "16918")
        task = select_task(
            row,
            [
                {"atflYr": "2025", "atchFileSn": "10"},
                {"atflYr": "ALL", "atchFileSn": "11"},
            ],
            "ALL",
        )
        self.assertEqual(task.attachment_serial, "11")
        self.assertEqual(task.file_year, "ALL")
        self.assertTrue(task.task_id.startswith("edss-"))

    def test_download_form_matches_all_fields_serialized_by_official_page(self):
        task = select_task(
            dataset("고등교육통계", "11595"),
            [{"atflYr": "ALL", "atchFileSn": "5007"}],
            "ALL",
        )
        fields = urllib.parse.parse_qs(
            encode_download_form(task).decode(),
            keep_blank_values=True,
        )
        self.assertEqual(
            fields,
            {
                "currentArtcClssCd": [""],
                "currentPvsnArtclCd": [""],
                "searchEduDataSeNm": [""],
                "atchFileSn": ["5007"],
                "domnCd": ["11595"],
                "atflYr": ["ALL"],
                "ldomnNm": ["대영역"],
                "searchDomnNm": [""],
                "searchPvsnArtclNm": [""],
                "searchPvsnYr": [""],
                "pageIndex": [""],
            },
        )


if __name__ == "__main__":
    unittest.main()
