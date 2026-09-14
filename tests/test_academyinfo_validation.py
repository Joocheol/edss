from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from academyinfo import ProbeDefaults, ValidationContext, run_validation  # noqa: E402
from academyinfo.validation import (  # noqa: E402
    MULTI_SCHOOL_OPERATIONS,
    YEAR_OMISSION_OPERATIONS,
)


SERVICES = (
    "StudentService",
    "BasicInformationService_2",
    "EducationResearchService",
    "FinancesService",
    "SchoolMajorInfoService",
    "EducationConditionService",
    "BasicInformationService_1",
    "IndustryAcademicCooperationService",
    "SchoolInfoService",
)


def make_endpoints() -> tuple[dict, ...]:
    named_operations = tuple(
        dict.fromkeys((*YEAR_OMISSION_OPERATIONS, *MULTI_SCHOOL_OPERATIONS))
    )
    endpoints = []
    for index, operation in enumerate(named_operations):
        if operation in MULTI_SCHOOL_OPERATIONS:
            strategy = "per_school_by_year"
        elif operation in {"getSchoolInfo", "getSchoolMajorInfo"}:
            strategy = "nationwide_by_year"
        elif operation == "getUniversityCode":
            strategy = "lookup_by_year"
        else:
            strategy = "lookup_once"
        endpoints.append(
            {
                "service": SERVICES[index % len(SERVICES)],
                "operation": operation,
                "scope_strategy": strategy,
                "endpoint_url": f"https://example.test/{operation}",
            }
        )
    while len(endpoints) < 103:
        index = len(endpoints)
        endpoints.append(
            {
                "service": SERVICES[index % len(SERVICES)],
                "operation": f"filler_{index:03d}",
                "scope_strategy": "lookup_once",
                "endpoint_url": f"https://example.test/filler_{index:03d}",
            }
        )
    return tuple(endpoints)


class FakeClient:
    def probe_endpoints(self, endpoints, defaults, *, max_workers):
        del max_workers
        results = []
        for endpoint in endpoints:
            operation = endpoint["operation"]
            total_count = {
                "getSchoolInfo": 2000,
                "getSchoolMajorInfo": 60919,
                "getUniversityCode": 377,
            }.get(operation, 1)
            results.append(
                {
                    "service": endpoint["service"],
                    "operation": operation,
                    "scope_strategy": endpoint["scope_strategy"],
                    "http_status": 200,
                    "result_code": "00",
                    "total_count": total_count,
                    "row_count": 1,
                    "elapsed_ms": 10,
                    "error": None,
                }
            )
        return sorted(results, key=lambda row: (row["service"], row["operation"]))

    def request_xml(self, endpoint_url, params, *, repeated_school_ids=None):
        operation = endpoint_url.rsplit("/", 1)[-1]
        if operation == "getSchoolMajorInfo" and params.get("numOfRows") == "500":
            start = 0 if params["pageNo"] == "1" else 487
            rows = [(('row_id', str(index)),) for index in range(start, start + 500)]
            return self._response(60919, rows=rows)
        if operation in YEAR_OMISSION_OPERATIONS and "svyYr" not in params:
            return self._response(0)
        school_value = params.get("schlId")
        if operation in MULTI_SCHOOL_OPERATIONS:
            if school_value is None or "," in school_value or "|" in school_value:
                return self._response(0)
            school_id = repeated_school_ids[0] if repeated_school_ids else school_value
            return self._response(1, school_id=school_id)
        return self._response(1)

    @staticmethod
    def _response(total_count, *, rows=None, school_id=None):
        if rows is None:
            row = (("schlId", school_id),) if school_id else (("value", "1"),)
            rows = [] if total_count == 0 else [row]
        return {
            "result_code": "00",
            "total_count": total_count,
            "rows": rows,
            "row_count": len(rows),
            "school_ids": [school_id] if school_id else [],
        }


class AcademyInfoValidationTests(unittest.TestCase):
    def test_complete_workflow_returns_expected_summary(self) -> None:
        context = ValidationContext(
            client=FakeClient(),
            endpoints=make_endpoints(),
            default_concurrency=2,
            max_concurrency=4,
        )
        result = run_validation(context, defaults=ProbeDefaults())
        summary = result.summary()

        self.assertEqual(summary["services"], 9)
        self.assertEqual(summary["operations_passed"], 103)
        self.assertEqual(summary["year_omission_zero"], "9/9")
        self.assertEqual(summary["school_omission_zero"], "6/6")
        self.assertEqual(summary["multi_school_comma_pipe_unsupported"], "6/6")
        self.assertEqual(summary["repeated_school_parameter_uses_first"], "6/6")
        self.assertEqual(summary["school_major_page_boundary_duplicates"], 13)
        self.assertEqual(summary["concurrency_4_passed"], "8/8")
        self.assertEqual(summary["created_data_files"], 0)


if __name__ == "__main__":
    unittest.main()
