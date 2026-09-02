import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import collect_academyinfo_school_major as collector


def response_xml(items, total=None):
    item_xml = "".join(
        "<item>" + "".join(f"<{key}>{value}</{key}>" for key, value in item.items()) + "</item>"
        for item in items
    )
    total = len(items) if total is None else total
    return (
        "<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>"
        f"<body><items>{item_xml}</items><numOfRows>9999</numOfRows><pageNo>1</pageNo>"
        f"<totalCount>{total}</totalCount></body></response>"
    ).encode()


class AcademyInfoSchoolMajorCollectorTests(unittest.TestCase):
    def args(self):
        return SimpleNamespace(
            num_rows=9999,
            max_requests=0,
            key_mode="auto",
            retries=0,
            timeout=1,
            delay=0,
            progress_every=0,
        )

    def test_cached_collection_preserves_graduate_school_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "2025" / "page_0001.xml"
            path.parent.mkdir(parents=True)
            path.write_bytes(
                response_xml(
                    [
                        {
                            "svyYr": "2025",
                            "schlNm": "가야대학교 보건대학원(김해)",
                            "schlKndNm": "특수대학원",
                            "korMjrNm": "간호학전공",
                            "pbnfDgriCrseDivNm": "석사",
                        }
                    ]
                )
            )
            rows, records = collector.collect_year(
                2025,
                {"base_url": "https://example.invalid"},
                {"path": collector.OPERATION},
                root,
                "unused",
                self.args(),
                [0],
                {},
            )
            self.assertEqual(rows[0]["schlNm"], "가야대학교 보건대학원(김해)")
            self.assertEqual(records[0]["item_count"], 1)

    def test_quality_summary_separates_graduate_kinds(self):
        rows = [
            {
                "svyYr": "2025",
                "schlNm": "가야대학교(김해)",
                "schlKndNm": "대학교",
                "korMjrNm": "간호학과",
                "pbnfDgriCrseDivNm": "학사",
            },
            {
                "svyYr": "2025",
                "schlNm": "가야대학교 보건대학원(김해)",
                "schlKndNm": "특수대학원",
                "korMjrNm": "간호학전공",
                "pbnfDgriCrseDivNm": "석사",
            },
        ]
        summary = collector.quality_summary(rows, 2025)
        self.assertEqual(summary["graduate_row_count"], 1)
        self.assertEqual(summary["graduate_school_count"], 1)
        self.assertFalse(summary["graduate_school_identifier_present"])

    def test_exact_duplicate_rows_are_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "2025" / "page_0001.xml"
            path.parent.mkdir(parents=True)
            item = {"svyYr": "2025", "schlNm": "중복대학원", "schlKndNm": "일반대학원"}
            path.write_bytes(response_xml([item, item]))
            rows, _records = collector.collect_year(
                2025,
                {"base_url": "https://example.invalid"},
                {"path": collector.OPERATION},
                root,
                "unused",
                self.args(),
                [0],
                {},
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(collector.quality_summary(rows, 2025)["exact_duplicate_row_count"], 1)


if __name__ == "__main__":
    unittest.main()
