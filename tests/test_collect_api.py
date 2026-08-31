import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import collect_api


def response_xml(items, total=0, page=1, rows=10, code="00", message="NORMAL SERVICE."):
    item_xml = "".join(
        "<item>" + "".join(f"<{key}>{value}</{key}>" for key, value in item.items()) + "</item>"
        for item in items
    )
    return (
        f"<response><header><resultCode>{code}</resultCode><resultMsg>{message}</resultMsg></header>"
        f"<body><items>{item_xml}</items><numOfRows>{rows}</numOfRows><pageNo>{page}</pageNo>"
        f"<totalCount>{total}</totalCount></body></response>"
    ).encode()


class ParseXmlTests(unittest.TestCase):
    def test_parses_items_and_pagination(self):
        parsed = collect_api.parse_xml(response_xml([{"schlId": "0000149", "svyYr": "2009"}], total=1))
        self.assertEqual(parsed["total_count"], 1)
        self.assertEqual(parsed["items"][0]["schlId"], "0000149")

    def test_normal_empty_response_is_not_auth_error(self):
        parsed = collect_api.parse_xml(response_xml([], total=0))
        self.assertEqual(parsed["result_code"], "00")
        self.assertEqual(parsed["items"], [])

    def test_common_auth_error_is_distinct(self):
        xml = b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
        with self.assertRaises(collect_api.ApiResponseError) as context:
            collect_api.parse_xml(xml)
        self.assertEqual(context.exception.code, "30")

    def test_encoded_key_is_normalized_without_logging_value(self):
        candidates = collect_api.key_candidates("abc%2Bdef%3D", "auto")
        self.assertIn(("normalized", "abc+def="), candidates)
        self.assertIn(("literal_encoded", "abc%2Bdef%3D"), candidates)

    def test_http_429_xml_is_reported_as_daily_limit_error(self):
        body = b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22</returnReasonCode><returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
        error = HTTPError("https://example.invalid/redacted", 429, "Too Many Requests", {}, BytesIO(body))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(collect_api.ApiResponseError) as context:
                collect_api.get_bytes("https://example.invalid/redacted", retries=0, timeout=1, delay=0)
        self.assertEqual(context.exception.code, "22")


class PaginationTests(unittest.TestCase):
    def args(self):
        return SimpleNamespace(num_rows=1, max_requests=10, key_mode="auto", retries=0, timeout=1, delay=0)

    def write_pages(self, root: Path, second_item: dict):
        base = root / "student" / "getComparisonEnrolledStudentCrntSt" / "2009"
        base.mkdir(parents=True)
        (base / "page_0001.xml").write_bytes(response_xml([{"schlId": "0000149", "indctId": "1"}], total=2, page=1, rows=1))
        (base / "page_0002.xml").write_bytes(response_xml([second_item], total=2, page=2, rows=1))

    def test_resume_from_cached_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "raw"
            self.write_pages(root, {"schlId": "0000149", "indctId": "2"})
            service = {"key": "student", "base_url": "https://example.invalid"}
            operation = {"path": "/getComparisonEnrolledStudentCrntSt"}
            summary = collect_api.collect_year(service, operation, "0000149", 2009, root, Path(temp) / "manifest.jsonl", "unused", self.args(), [0])
            self.assertEqual(summary["item_count"], 2)
            self.assertEqual(summary["pages"], 2)

    def test_duplicate_items_across_pages_raise(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "raw"
            self.write_pages(root, {"schlId": "0000149", "indctId": "1"})
            service = {"key": "student", "base_url": "https://example.invalid"}
            operation = {"path": "/getComparisonEnrolledStudentCrntSt"}
            with self.assertRaises(collect_api.DuplicateItemsError):
                collect_api.collect_year(service, operation, "0000149", 2009, root, Path(temp) / "manifest.jsonl", "unused", self.args(), [0])


if __name__ == "__main__":
    unittest.main()
