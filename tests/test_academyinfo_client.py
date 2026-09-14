from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from academyinfo.client import (  # noqa: E402
    AcademyInfoClient,
    ProbeDefaults,
    _parse_xml_response,
    load_service_key,
    representative_params,
)


class AcademyInfoClientTests(unittest.TestCase):
    def test_load_service_key_decodes_encoded_value(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("IGNORED=x\nACADEMYINFO_SERVICE_KEY=abc%2B123%3D\n", encoding="utf-8")
            self.assertEqual(load_service_key(path), "abc+123=")

    def test_representative_params_cover_scope_strategies(self) -> None:
        defaults = ProbeDefaults()
        expected = {
            "per_school_by_year": {"schlId": "0000027", "svyYr": "2025"},
            "regional_by_school_type": {"schlDivCd": "01"},
            "regional_all_school_types": {},
            "nationwide_by_year": {"svyYr": "2025", "schlKrnNm": ""},
            "lookup_by_year": {"svyYr": "2025"},
            "lookup_once": {},
        }
        for strategy, additions in expected.items():
            with self.subTest(strategy=strategy):
                params = representative_params(
                    {"scope_strategy": strategy, "operation": "example"},
                    defaults,
                )
                self.assertEqual(params, {"pageNo": "1", "numOfRows": "1", **additions})

    def test_configured_fixed_parameter_is_applied(self) -> None:
        params = representative_params(
            {
                "scope_strategy": "per_school_by_year",
                "operation": "getComparisonFullTimeFacultyResearchCrntSt",
                "fixed_params": {"indctId": "33"},
            },
            ProbeDefaults(),
        )
        self.assertEqual(params["indctId"], "33")

    def test_repeated_school_query_preserves_order_and_hides_key_from_params(self) -> None:
        client = AcademyInfoClient("secret+key")
        query = client._build_query(
            {"serviceKey": "caller-value", "schlId": "0000027", "svyYr": "2025"},
            ["0000027", "0000014"],
        )
        parsed = parse_qs(query)
        self.assertEqual(parsed["serviceKey"], ["secret+key"])
        self.assertEqual(parsed["schlId"], ["0000027", "0000014"])

    def test_xml_response_preserves_exact_rows_and_school_ids(self) -> None:
        payload = b"""<response><header><resultCode>00</resultCode></header><body>
        <items><item><schlId>0000027</schlId><value>1</value></item></items>
        <totalCount>1</totalCount></body></response>"""
        result = _parse_xml_response(payload, 200, 12)
        self.assertEqual(result["result_code"], "00")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["school_ids"], ["0000027"])
        self.assertEqual(dict(result["rows"][0]), {"schlId": "0000027", "value": "1"})


if __name__ == "__main__":
    unittest.main()
