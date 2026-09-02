import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import collect_academyinfo_enrollment as collector


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


class AcademyInfoEnrollmentCollectorTests(unittest.TestCase):
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

    def test_cached_code_list_requires_unique_nonblank_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "2023" / "codes" / "page_0001.xml"
            path.parent.mkdir(parents=True)
            path.write_bytes(response_xml([{"schlId": "0001"}, {"schlId": "0002"}]))
            rows, records = collector.collect_code_list(
                2023,
                {"base_url": "https://example.invalid"},
                {"path": "/codes"},
                root,
                "unused",
                self.args(),
                [0],
                {},
            )
            self.assertEqual([row["schlId"] for row in rows], ["0001", "0002"])
            self.assertEqual(records[0]["item_count"], 2)

    def test_cached_enrollment_preserves_leading_zero_school_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "2023" / "enrollment" / "0000149.xml"
            path.parent.mkdir(parents=True)
            path.write_bytes(
                response_xml(
                    [{"schlId": "0000149", "svyYr": "2023", "indctId": "9", "indctVal1": "27409"}]
                )
            )
            code = {field: "" for field in collector.OUTPUT_FIELDS}
            code.update({"schlId": "0000149", "svyYr": "2023", "schlKrnNm": "연세대학교"})
            row, record = collector.collect_enrollment(
                code,
                2023,
                {"base_url": "https://example.invalid"},
                {"path": "/enrollment"},
                root,
                "unused",
                self.args(),
                [0],
                {},
            )
            self.assertEqual(row["schlId"], "0000149")
            self.assertEqual(row["indctVal1"], "27409")
            self.assertEqual(record["item_count"], 1)

    def test_response_key_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "2023" / "enrollment" / "0000149.xml"
            path.parent.mkdir(parents=True)
            path.write_bytes(response_xml([{"schlId": "9999999", "svyYr": "2023", "indctVal1": "1"}]))
            code = {field: "" for field in collector.OUTPUT_FIELDS}
            code.update({"schlId": "0000149", "svyYr": "2023"})
            with self.assertRaises(RuntimeError):
                collector.collect_enrollment(
                    code,
                    2023,
                    {"base_url": "https://example.invalid"},
                    {"path": "/enrollment"},
                    root,
                    "unused",
                    self.args(),
                    [0],
                    {},
                )


if __name__ == "__main__":
    unittest.main()
