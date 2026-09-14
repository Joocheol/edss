from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from academyinfo.collection import (  # noqa: E402
    AcademyInfoCollector,
    CollectionError,
    CollectionPolicy,
    TaskExecutionError,
    VerifiedRowLimitError,
    school_ids_from_xml,
)
from academyinfo.downloader import TemporaryXmlDownload, XmlResponseMetadata  # noqa: E402
from academyinfo.planning import build_collection_tasks  # noqa: E402
from academyinfo.storage import AcademyInfoStorage  # noqa: E402


def endpoint(
    operation: str,
    strategy: str = "nationwide_by_year",
    output_fields: list[str] | None = None,
) -> dict[str, object]:
    return {
        "data_id": "15158963",
        "service": "ExampleService",
        "operation": operation,
        "scope_strategy": strategy,
        "endpoint_url": f"https://example.test/{operation}",
        "output_fields": ["value"] if output_fields is None else output_fields,
    }


def response_xml(items: list[dict[str, str]], *, total_count: int | None = None) -> bytes:
    rows = "".join(
        "<item>"
        + "".join(f"<{key}>{value}</{key}>" for key, value in item.items())
        + "</item>"
        for item in items
    )
    total = len(items) if total_count is None else total_count
    return (
        "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg>"
        f"</header><body><items>{rows}</items><totalCount>{total}</totalCount>"
        "</body></response>"
    ).encode()


class FakeDownloader:
    def __init__(self, directory: Path, payloads: list[bytes]) -> None:
        self.directory = directory
        self.payloads = list(payloads)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def download_to_temporary_file(
        self, endpoint_url: str, params: dict[str, object]
    ) -> TemporaryXmlDownload:
        self.calls.append((endpoint_url, dict(params)))
        if not self.payloads:
            raise AssertionError("unexpected download")
        payload = self.payloads.pop(0)
        descriptor, filename = tempfile.mkstemp(dir=self.directory, suffix=".xml")
        path = Path(filename)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        from academyinfo.downloader import parse_xml_metadata

        result_code, result_message, total_count, item_count = parse_xml_metadata(path)
        root = ET.fromstring(payload)
        rows = [
            tuple(
                (child.tag.rsplit("}", 1)[-1], (child.text or "").strip())
                for child in list(item)
            )
            for item in root.iter()
            if item.tag.rsplit("}", 1)[-1] == "item"
        ]
        field_names = tuple(sorted({name for row in rows for name, _value in row}))
        return TemporaryXmlDownload(
            path=path,
            metadata=XmlResponseMetadata(
                http_status=200,
                bytes_written=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                result_code=result_code,
                result_message=result_message,
                total_count=total_count,
                item_count=item_count,
                elapsed_ms=1,
                attempts=1,
                field_names=field_names,
                duplicate_row_count=len(rows) - len(set(rows)),
            ),
        )


class AcademyInfoCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.storage = AcademyInfoStorage(self.root)
        self.config = {"endpoints": [endpoint("getExample")]}
        self.policy = CollectionPolicy(concurrency=2, max_concurrency=4)

    def collector(self, payloads: list[bytes]) -> tuple[AcademyInfoCollector, FakeDownloader]:
        downloader = FakeDownloader(self.root, payloads)
        collector = AcademyInfoCollector(
            endpoint_config=self.config,
            downloader=downloader,  # type: ignore[arg-type]
            storage=self.storage,
            policy=self.policy,
        )
        return collector, downloader

    def test_probe_then_full_response_is_stored_and_resume_uses_no_network(self) -> None:
        probe = response_xml([{"value": "first"}], total_count=2)
        full = response_xml(
            [{"value": "first"}, {"value": "second"}], total_count=2
        )
        collector, downloader = self.collector([probe, full])
        task = build_collection_tasks(self.config, years=["2025"])[0]

        first = collector.collect_task(task)
        second = collector.collect_task(task)

        self.assertEqual(first.status, "stored")
        self.assertEqual(first.request_count, 2)
        self.assertEqual(second.status, "skipped")
        self.assertEqual(second.request_count, 0)
        self.assertEqual(len(downloader.calls), 2)
        self.assertEqual(downloader.calls[0][1]["numOfRows"], "1")
        self.assertEqual(downloader.calls[1][1]["numOfRows"], "2")
        self.assertEqual(first.record.row_count, 2)
        self.assertEqual(first.record.total_count, 2)
        self.assertEqual(first.record.params["svyYr"], "2025")
        self.assertNotIn("serviceKey", first.record.params)
        self.assertEqual(len(self.storage.records()), 1)

    def test_zero_row_probe_is_the_final_immutable_response(self) -> None:
        collector, downloader = self.collector([response_xml([], total_count=0)])
        task = build_collection_tasks(self.config, years=["2025"])[0]

        result = collector.collect_task(task)

        self.assertEqual(result.status, "stored")
        self.assertEqual(result.request_count, 1)
        self.assertEqual(result.record.row_count, 0)
        self.assertEqual(result.record.column_count, 0)
        self.assertEqual(result.record.field_names, ())
        self.assertEqual(len(downloader.calls), 1)

    def test_exact_duplicate_rows_are_preserved_and_reported(self) -> None:
        probe = response_xml([{"value": "same"}], total_count=2)
        full = response_xml(
            [{"value": "same"}, {"value": "same"}], total_count=2
        )
        collector, _downloader = self.collector([probe, full])
        task = build_collection_tasks(self.config, years=["2025"])[0]

        result = collector.collect_task(task)

        self.assertEqual(result.record.row_count, 2)
        self.assertEqual(result.record.column_count, 1)
        self.assertEqual(result.record.field_names, ("value",))
        self.assertEqual(result.record.duplicate_row_count, 1)

    def test_omitted_declared_fields_are_allowed_and_actual_fields_are_recorded(
        self,
    ) -> None:
        config = {
            "endpoints": [endpoint("getExample", output_fields=["optional", "value"])]
        }
        downloader = FakeDownloader(
            self.root, [response_xml([{"value": "x"}], total_count=1)]
        )
        collector = AcademyInfoCollector(
            endpoint_config=config,
            downloader=downloader,  # type: ignore[arg-type]
            storage=self.storage,
            policy=self.policy,
        )
        task = build_collection_tasks(config, years=["2025"])[0]

        result = collector.collect_task(task)

        self.assertEqual(result.record.field_names, ("value",))
        self.assertEqual(result.record.column_count, 1)

    def test_undeclared_response_fields_are_rejected(self) -> None:
        collector, _downloader = self.collector(
            [response_xml([{"unexpected": "x"}], total_count=1)]
        )
        task = build_collection_tasks(self.config, years=["2025"])[0]

        with self.assertRaisesRegex(TaskExecutionError, "not declared in config"):
            collector.collect_task(task)

        self.assertEqual(self.storage.records(), [])

    def test_response_above_reviewed_limit_is_rejected_without_raw_artifact(self) -> None:
        policy = CollectionPolicy(
            concurrency=1, max_concurrency=4, probe_page_size=1, max_verified_rows=2
        )
        downloader = FakeDownloader(
            self.root, [response_xml([{"value": "x"}], total_count=3)]
        )
        collector = AcademyInfoCollector(
            endpoint_config=self.config,
            downloader=downloader,  # type: ignore[arg-type]
            storage=self.storage,
            policy=policy,
        )
        task = build_collection_tasks(self.config, years=["2025"])[0]

        with self.assertRaisesRegex(VerifiedRowLimitError, "exceeds reviewed"):
            collector.collect_task(task)

        self.assertEqual(self.storage.records(), [])
        self.assertEqual(list(self.root.glob("*.xml")), [])

    def test_incomplete_full_response_fails_and_removes_all_temporary_files(self) -> None:
        probe = response_xml([{"value": "first"}], total_count=2)
        incomplete = response_xml([{"value": "first"}], total_count=2)
        collector, downloader = self.collector([probe, incomplete])
        task = build_collection_tasks(self.config, years=["2025"])[0]

        with self.assertRaisesRegex(TaskExecutionError, "received 1 item") as raised:
            collector.collect_task(task)

        self.assertEqual(raised.exception.request_count, 2)
        self.assertEqual(len(downloader.calls), 2)
        self.assertEqual(self.storage.records(), [])
        self.assertEqual(list(self.root.glob("*.xml")), [])

    def test_explicit_school_plan_is_side_effect_free(self) -> None:
        per_school_config = {
            "endpoints": [endpoint("perSchool", "per_school_by_year")]
        }
        collector, downloader = self.collector([response_xml([])])
        collector.endpoint_config = per_school_config

        tasks = collector.plan(years=["2025"], school_ids=["0000027"])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(downloader.calls, [])
        self.assertFalse((self.root / "data").exists())

    def test_school_discovery_preserves_leading_zeroes_and_builds_per_school_tasks(self) -> None:
        university = endpoint(
            "getUniversityCode", output_fields=["schlId", "svyYr"]
        )
        per_school = endpoint("perSchool", "per_school_by_year")
        config = {"endpoints": [university, per_school]}
        payload = response_xml(
            [
                {"schlId": "0000027", "svyYr": "2025"},
                {"schlId": "0000014", "svyYr": "2025"},
            ],
            total_count=2,
        )
        downloader = FakeDownloader(
            self.root,
            [
                response_xml(
                    [{"schlId": "0000027", "svyYr": "2025"}], total_count=2
                ),
                payload,
            ],
        )
        collector = AcademyInfoCollector(
            endpoint_config=config,
            downloader=downloader,  # type: ignore[arg-type]
            storage=self.storage,
            policy=self.policy,
        )

        discovery = collector.discover_schools(["2025"], allow_download=True)
        tasks = collector.plan(
            years=["2025"],
            operations=["perSchool"],
            schools_by_year=discovery.schools_by_year,
        )

        self.assertEqual(
            [task.request_params()["schlId"] for task in tasks],
            ["0000014", "0000027"],
        )
        code_record = self.storage.records()[0]
        self.assertEqual(
            school_ids_from_xml(self.root / code_record.raw_path),
            ["0000014", "0000027"],
        )
        offline = AcademyInfoCollector(
            endpoint_config=config,
            downloader=None,
            storage=self.storage,
            policy=self.policy,
        )
        cached = offline.discover_schools(["2025"], allow_download=False)
        self.assertEqual(cached.schools_by_year, discovery.schools_by_year)
        self.assertEqual(cached.results[0].request_count, 0)

    def test_school_discovery_rejects_duplicate_or_wrong_year_rows(self) -> None:
        duplicate = self.root / "duplicate.xml"
        duplicate.write_bytes(
            response_xml(
                [
                    {"schlId": "0000027", "svyYr": "2025"},
                    {"schlId": "0000027", "svyYr": "2025"},
                ]
            )
        )
        with self.assertRaisesRegex(CollectionError, "duplicate"):
            school_ids_from_xml(
                duplicate, expected_year="2025", expected_row_count=2
            )

        wrong_year = self.root / "wrong-year.xml"
        wrong_year.write_bytes(
            response_xml([{"schlId": "0000027", "svyYr": "2024"}])
        )
        with self.assertRaisesRegex(CollectionError, "does not match"):
            school_ids_from_xml(
                wrong_year, expected_year="2025", expected_row_count=1
            )

    def test_run_continues_after_a_task_failure(self) -> None:
        config = {
            "endpoints": [endpoint("a"), endpoint("b")],
        }
        downloader = FakeDownloader(
            self.root,
            [
                response_xml([{"value": "x"}], total_count=1),
                b"<response><resultCode>99</resultCode><totalCount>0</totalCount></response>",
            ],
        )
        collector = AcademyInfoCollector(
            endpoint_config=config,
            downloader=downloader,  # type: ignore[arg-type]
            storage=self.storage,
            policy=self.policy,
        )
        tasks = build_collection_tasks(config, years=["2025"])

        run = collector.run(tasks)

        self.assertEqual(run.summary()["planned"], 2)
        self.assertEqual(run.summary()["completed"], 1)
        self.assertEqual(run.summary()["failed"], 1)
        self.assertEqual(run.summary()["http_requests"], 2)

    def test_fatal_run_waits_for_running_workers_before_returning(self) -> None:
        config = {"endpoints": [endpoint("fatal"), endpoint("alreadyRunning")]}
        tasks = build_collection_tasks(config, years=["2025"])
        barrier = threading.Barrier(2)
        late_worker_finished: list[bool] = []

        class FatalCollector(AcademyInfoCollector):
            def collect_task(self, task):  # type: ignore[no-untyped-def]
                barrier.wait(timeout=1)
                if task.operation == "fatal":
                    raise VerifiedRowLimitError("review required")
                time.sleep(0.02)
                late_worker_finished.append(True)
                raise TaskExecutionError("expected", "finished", request_count=0)

        collector = FatalCollector(
            endpoint_config=config,
            downloader=None,
            storage=self.storage,
            policy=self.policy,
        )

        with self.assertRaises(VerifiedRowLimitError):
            collector.run(tasks)

        self.assertEqual(late_worker_finished, [True])


if __name__ == "__main__":
    unittest.main()
