from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from academyinfo.planning import (  # noqa: E402
    CollectionPlanningError,
    SUPPORTED_SCOPE_STRATEGIES,
    build_collection_tasks,
)


def endpoint(operation: str, strategy: str) -> dict[str, object]:
    return {
        "data_id": "15158684",
        "service": "ExampleService",
        "operation": operation,
        "scope_strategy": strategy,
        "endpoint_url": f"https://example.test/{operation}",
    }


class AcademyInfoPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.endpoints = [
            endpoint("perSchool", "per_school_by_year"),
            endpoint("nationwide", "nationwide_by_year"),
            endpoint("regionalTyped", "regional_by_school_type"),
            endpoint("regionalAll", "regional_all_school_types"),
            endpoint("lookup", "lookup_once"),
            endpoint("lookupYear", "lookup_by_year"),
        ]

    def test_all_six_strategies_expand_with_stable_deduplication(self) -> None:
        config = {"endpoints": [*reversed(self.endpoints), self.endpoints[0]]}
        tasks = build_collection_tasks(
            config,
            years=["2025", "2024", "2025"],
            schools_by_year={
                "2025": ["0000027", "0000027"],
                "2024": ["0000014", "0000001", "0000014"],
            },
            school_division_codes=["02", "01", "02"],
        )

        self.assertEqual(SUPPORTED_SCOPE_STRATEGIES, {
            "per_school_by_year",
            "nationwide_by_year",
            "regional_by_school_type",
            "regional_all_school_types",
            "lookup_once",
            "lookup_by_year",
        })
        self.assertEqual(len(tasks), 11)
        self.assertEqual(len({task.task_id for task in tasks}), 11)
        self.assertTrue(
            all(re.fullmatch(r"academyinfo-[0-9a-f]{24}", task.task_id) for task in tasks)
        )

        per_school = [task for task in tasks if task.operation == "perSchool"]
        self.assertEqual(
            [task.request_params() for task in per_school],
            [
                {"schlId": "0000001", "svyYr": "2024"},
                {"schlId": "0000014", "svyYr": "2024"},
                {"schlId": "0000027", "svyYr": "2025"},
            ],
        )
        self.assertEqual(
            [task.request_params() for task in tasks if task.operation == "nationwide"],
            [{"svyYr": "2024"}, {"svyYr": "2025"}],
        )
        self.assertEqual(
            [
                task.request_params()
                for task in tasks
                if task.operation == "regionalTyped"
            ],
            [{"schlDivCd": "01"}, {"schlDivCd": "02"}],
        )
        self.assertEqual(
            [task.request_params() for task in tasks if task.operation == "regionalAll"],
            [{}],
        )
        self.assertEqual(
            [task.request_params() for task in tasks if task.operation == "lookup"],
            [{}],
        )
        self.assertEqual(
            [task.request_params() for task in tasks if task.operation == "lookupYear"],
            [{"svyYr": "2024"}, {"svyYr": "2025"}],
        )

        second_run = build_collection_tasks(
            {"endpoints": list(self.endpoints)},
            years=["2024", "2025"],
            schools_by_year={
                "2024": ["0000001", "0000014"],
                "2025": ["0000027"],
            },
            school_division_codes=["01", "02"],
        )
        self.assertEqual(tasks, second_run)
        self.assertNotIn("serviceKey", repr(tasks))

    def test_string_types_are_enforced_to_preserve_leading_zeroes(self) -> None:
        with self.assertRaisesRegex(CollectionPlanningError, "only non-empty strings"):
            build_collection_tasks(
                {"endpoints": [endpoint("perSchool", "per_school_by_year")]},
                years=[2025],  # type: ignore[list-item]
                schools_by_year={"2025": ["0000027"]},
            )
        with self.assertRaisesRegex(CollectionPlanningError, "only non-empty strings"):
            build_collection_tasks(
                {"endpoints": [endpoint("perSchool", "per_school_by_year")]},
                years=["2025"],
                schools_by_year={"2025": [27]},  # type: ignore[list-item]
            )

    def test_missing_strategy_inputs_raise_explicit_errors(self) -> None:
        with self.assertRaisesRegex(CollectionPlanningError, "Missing required input: years"):
            build_collection_tasks(
                {"endpoints": [endpoint("nationwide", "nationwide_by_year")]}
            )
        with self.assertRaisesRegex(
            CollectionPlanningError, "Missing required school list for survey year 2024"
        ):
            build_collection_tasks(
                {"endpoints": [endpoint("perSchool", "per_school_by_year")]},
                years=["2024", "2025"],
                schools_by_year={"2025": ["0000027"]},
            )
        with self.assertRaisesRegex(
            CollectionPlanningError, "Missing required input: school_division_codes"
        ):
            build_collection_tasks(
                {"endpoints": [endpoint("regional", "regional_by_school_type")]}
            )

    def test_unknown_strategy_and_conflicting_duplicate_are_rejected(self) -> None:
        with self.assertRaisesRegex(CollectionPlanningError, "Unknown scope_strategy"):
            build_collection_tasks(
                {"endpoints": [endpoint("bad", "not_a_strategy")]}
            )

        duplicate = endpoint("lookup", "lookup_once")
        conflicting = {**duplicate, "endpoint_url": "https://other.test/lookup"}
        with self.assertRaisesRegex(CollectionPlanningError, "Conflicting duplicate"):
            build_collection_tasks({"endpoints": [duplicate, conflicting]})

    def test_lookup_once_needs_no_scope_inputs(self) -> None:
        tasks = build_collection_tasks(
            {"endpoints": [endpoint("lookup", "lookup_once")]}
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].request_params(), {})

    def test_fixed_params_are_validated_and_merged_into_every_task(self) -> None:
        configured = endpoint("indicator", "per_school_by_year")
        configured["fixed_params"] = {"indctId": "33", "schlKrnNm": ""}
        tasks = build_collection_tasks(
            {"endpoints": [configured]},
            years=["2024", "2025"],
            schools_by_year={"2024": ["0000014"], "2025": ["0000027"]},
        )

        self.assertEqual(
            [task.request_params() for task in tasks],
            [
                {
                    "indctId": "33",
                    "schlId": "0000014",
                    "schlKrnNm": "",
                    "svyYr": "2024",
                },
                {
                    "indctId": "33",
                    "schlId": "0000027",
                    "schlKrnNm": "",
                    "svyYr": "2025",
                },
            ],
        )
        without_fixed = endpoint("indicator", "per_school_by_year")
        other_tasks = build_collection_tasks(
            {"endpoints": [without_fixed]},
            years=["2024"],
            schools_by_year={"2024": ["0000014"]},
        )
        self.assertNotEqual(tasks[0].task_id, other_tasks[0].task_id)

    def test_fixed_params_reject_secrets_scope_keys_and_non_strings(self) -> None:
        for key in ("serviceKey", "api_key", "Authorization"):
            with self.subTest(key=key), self.assertRaisesRegex(
                CollectionPlanningError, "must not contain secret parameter"
            ):
                configured = endpoint("lookup", "lookup_once")
                configured["fixed_params"] = {key: "secret"}
                build_collection_tasks({"endpoints": [configured]})

        with self.assertRaisesRegex(
            CollectionPlanningError, "must not contain scope parameter"
        ):
            configured = endpoint("lookup", "lookup_once")
            configured["fixed_params"] = {"svyYr": "2025"}
            build_collection_tasks({"endpoints": [configured]})

        with self.assertRaisesRegex(CollectionPlanningError, "must be a string"):
            configured = endpoint("lookup", "lookup_once")
            configured["fixed_params"] = {"indctId": 33}
            build_collection_tasks({"endpoints": [configured]})

        with self.assertRaisesRegex(CollectionPlanningError, "must be a mapping"):
            configured = endpoint("lookup", "lookup_once")
            configured["fixed_params"] = [("indctId", "33")]
            build_collection_tasks({"endpoints": [configured]})


if __name__ == "__main__":
    unittest.main()
