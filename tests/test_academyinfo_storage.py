from __future__ import annotations

import hashlib
import json
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from academyinfo.storage import (  # noqa: E402
    AcademyInfoStorage,
    CollectionMetadata,
    DownloadIntegrityError,
    ManifestIntegrityError,
    RawPathConflictError,
    TaskConflictError,
    UnsafeParameterError,
    safe_params,
)


class AcademyInfoStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.storage = AcademyInfoStorage(self.root)

    @staticmethod
    def metadata(task_id: str = "SchoolInfo-2025-all") -> CollectionMetadata:
        return CollectionMetadata(
            task_id=task_id,
            data_id="15158963",
            service="SchoolInfoService",
            operation="getSchoolInfo",
            endpoint_url="https://apis.data.go.kr/B552090/SchoolInfoService/getSchoolInfo",
            scope_strategy="nationwide_by_year",
            params={
                "serviceKey": "must-never-be-recorded",
                "schlId": "0000027",
                "schlDivCd": "01",
                "svyYr": "2025",
                "pageNo": "1",
            },
            http_status=200,
            result_code="00",
            total_count=1,
            row_count=1,
            column_count=2,
            field_names=("schlId", "svyYr"),
            duplicate_row_count=0,
            collected_at="2026-09-14T12:34:56Z",
        )

    def test_save_publishes_raw_bytes_and_complete_safe_manifest(self) -> None:
        payload = b"<response><schlId>0000027</schlId></response>"
        result = self.storage.save_bytes(
            payload,
            "data/raw/academyinfo/school-info.xml",
            self.metadata(),
        )

        self.assertEqual(result.status, "stored")
        self.assertFalse(result.skipped)
        self.assertEqual(
            (self.root / "data/raw/academyinfo/school-info.xml").read_bytes(), payload
        )
        lines = (self.root / "data/metadata/academyinfo_manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(lines), 1)
        manifest = json.loads(lines[0])
        self.assertEqual(
            set(manifest),
            {
                "task_id",
                "data_id",
                "service",
                "operation",
                "endpoint_url",
                "scope_strategy",
                "raw_path",
                "sha256",
                "bytes",
                "collected_at",
                "http_status",
                "result_code",
                "total_count",
                "row_count",
                "column_count",
                "field_names",
                "duplicate_row_count",
                "params",
            },
        )
        self.assertEqual(manifest["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(manifest["bytes"], len(payload))
        self.assertEqual(manifest["raw_path"], "data/raw/academyinfo/school-info.xml")
        self.assertEqual(manifest["data_id"], "15158963")
        self.assertEqual(manifest["service"], "SchoolInfoService")
        self.assertEqual(manifest["operation"], "getSchoolInfo")
        self.assertEqual(manifest["scope_strategy"], "nationwide_by_year")
        self.assertEqual(manifest["column_count"], 2)
        self.assertEqual(manifest["field_names"], ["schlId", "svyYr"])
        self.assertEqual(manifest["duplicate_row_count"], 0)
        self.assertNotIn("?", manifest["endpoint_url"])
        self.assertNotIn("serviceKey", manifest["params"])
        self.assertEqual(manifest["params"]["schlId"], "0000027")
        self.assertEqual(manifest["params"]["schlDivCd"], "01")
        self.assertEqual(list((self.root / "data/raw/academyinfo").glob("*.partial")), [])

    def test_exact_task_and_checksum_is_skipped_without_second_file_or_record(self) -> None:
        payload = b"same response"
        first = self.storage.save_bytes(
            payload, "data/raw/academyinfo/first.xml", self.metadata()
        )
        second = self.storage.save_bytes(
            payload, "data/raw/academyinfo/second.xml", self.metadata()
        )

        self.assertEqual(first.status, "stored")
        self.assertTrue(second.skipped)
        self.assertEqual(second.record.raw_path, "data/raw/academyinfo/first.xml")
        self.assertFalse((self.root / "data/raw/academyinfo/second.xml").exists())
        self.assertEqual(len(self.storage.records()), 1)

    def test_completed_record_supports_pre_network_resume_check(self) -> None:
        self.assertIsNone(self.storage.completed_record("not-yet-collected"))
        payload = b"complete response"
        stored = self.storage.save_bytes(
            payload, "data/raw/academyinfo/complete.xml", self.metadata()
        )

        completed = self.storage.completed_record(stored.record.task_id)

        self.assertEqual(completed, stored.record)
        self.assertEqual(completed.sha256, hashlib.sha256(payload).hexdigest())

    def test_completed_record_rejects_multiple_checksums_for_one_task(self) -> None:
        first_payload = b"first"
        first = self.storage.save_bytes(
            first_payload, "data/raw/academyinfo/first.xml", self.metadata()
        )
        second_payload = b"second"
        second_path = self.root / "data/raw/academyinfo/second.xml"
        second_path.write_bytes(second_payload)
        conflicting = {
            **first.record.__dict__,
            "raw_path": "data/raw/academyinfo/second.xml",
            "sha256": hashlib.sha256(second_payload).hexdigest(),
            "bytes": len(second_payload),
        }
        with (self.root / "data/metadata/academyinfo_manifest.jsonl").open(
            "a", encoding="utf-8"
        ) as manifest:
            manifest.write(json.dumps(conflicting) + "\n")

        with self.assertRaises(TaskConflictError):
            self.storage.completed_record(first.record.task_id)

    def test_completed_record_rejects_damaged_raw_file(self) -> None:
        raw_path = "data/raw/academyinfo/complete.xml"
        stored = self.storage.save_bytes(b"complete", raw_path, self.metadata())
        (self.root / raw_path).write_bytes(b"damaged")

        with self.assertRaises(ManifestIntegrityError):
            self.storage.completed_record(stored.record.task_id)

    def test_save_file_consumes_download_and_checks_streaming_facts(self) -> None:
        payload = b"downloaded response"
        temporary = self.root / "download.xml"
        temporary.write_bytes(payload)
        checksum = hashlib.sha256(payload).hexdigest()

        result = self.storage.save_file(
            temporary,
            "data/raw/academyinfo/response.xml",
            self.metadata(),
            expected_sha256=checksum,
            expected_bytes=len(payload),
        )

        self.assertEqual(result.status, "stored")
        self.assertFalse(temporary.exists())
        self.assertEqual(
            (self.root / "data/raw/academyinfo/response.xml").read_bytes(), payload
        )

    def test_save_file_retains_download_when_integrity_check_fails(self) -> None:
        temporary = self.root / "download.xml"
        temporary.write_bytes(b"response")

        with self.assertRaises(DownloadIntegrityError):
            self.storage.save_file(
                temporary,
                "data/raw/academyinfo/response.xml",
                self.metadata(),
                expected_sha256="0" * 64,
            )

        self.assertTrue(temporary.exists())
        self.assertFalse((self.root / "data/raw/academyinfo/response.xml").exists())
        self.assertEqual(self.storage.records(), [])

    def test_same_task_with_different_checksum_is_an_explicit_conflict(self) -> None:
        original = b"original"
        raw_path = "data/raw/academyinfo/response.xml"
        self.storage.save_bytes(original, raw_path, self.metadata())

        with self.assertRaises(TaskConflictError):
            self.storage.save_bytes(b"changed", raw_path, self.metadata())

        self.assertEqual((self.root / raw_path).read_bytes(), original)
        self.assertEqual(len(self.storage.records()), 1)

    def test_existing_raw_path_is_never_overwritten(self) -> None:
        raw_path = "data/raw/academyinfo/response.xml"
        destination = self.root / raw_path
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"pre-existing")

        with self.assertRaises(RawPathConflictError):
            self.storage.save_bytes(b"new", raw_path, self.metadata("new-task"))

        self.assertEqual(destination.read_bytes(), b"pre-existing")
        self.assertEqual(self.storage.records(), [])

    def test_matching_orphan_raw_file_recovers_manifest_after_crash(self) -> None:
        payload = b"published before manifest append"
        raw_path = "data/raw/academyinfo/orphan.xml"
        destination = self.root / raw_path
        destination.parent.mkdir(parents=True)
        destination.write_bytes(payload)
        original_inode = destination.stat().st_ino
        temporary = self.root / "new-download.xml"
        temporary.write_bytes(payload)

        result = self.storage.save_file(
            temporary,
            raw_path,
            self.metadata("orphan-task"),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            expected_bytes=len(payload),
        )

        self.assertEqual(result.status, "stored")
        self.assertEqual(destination.stat().st_ino, original_inode)
        self.assertFalse(temporary.exists())
        self.assertEqual(self.storage.completed_record("orphan-task"), result.record)

    def test_different_orphan_raw_file_remains_an_explicit_conflict(self) -> None:
        raw_path = "data/raw/academyinfo/orphan.xml"
        destination = self.root / raw_path
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"old content")
        temporary = self.root / "new-download.xml"
        temporary.write_bytes(b"new content")

        with self.assertRaises(RawPathConflictError):
            self.storage.save_file(
                temporary, raw_path, self.metadata("orphan-task")
            )

        self.assertEqual(destination.read_bytes(), b"old content")
        self.assertTrue(temporary.exists())
        self.assertEqual(self.storage.records(), [])

    def test_resume_detects_missing_or_changed_raw_artifact(self) -> None:
        payload = b"response"
        raw_path = "data/raw/academyinfo/response.xml"
        result = self.storage.save_bytes(payload, raw_path, self.metadata())
        (self.root / raw_path).write_bytes(b"tampered")

        with self.assertRaises(ManifestIntegrityError):
            self.storage.resume_record(result.record.task_id, result.record.sha256)

    def test_identifier_params_must_be_strings_and_secrets_are_removed(self) -> None:
        with self.assertRaises(UnsafeParameterError):
            safe_params({"schlId": 27})

        cleaned = safe_params(
            {
                "service_key": "secret",
                "API-KEY": "secret-too",
                "schlId": "0000027",
                "campusCode": "001",
            }
        )
        self.assertEqual(cleaned, {"schlId": "0000027", "campusCode": "001"})

    def test_endpoint_url_rejects_query_credentials(self) -> None:
        values = self.metadata().__dict__
        with self.assertRaisesRegex(ValueError, "query or fragment"):
            CollectionMetadata(
                **{
                    **values,
                    "endpoint_url": "https://example.test/api?serviceKey=secret",
                }
            )

    def test_shape_metadata_contract_is_enforced(self) -> None:
        values = self.metadata().__dict__
        invalid_overrides = (
            {"column_count": 1},
            {"field_names": ("svyYr", "schlId")},
            {"field_names": ("schlId", "schlId")},
            {"field_names": ("", "svyYr")},
            {"duplicate_row_count": -1},
            {"duplicate_row_count": 2},
            {"duplicate_row_count": 0.5},
            {"column_count": True},
        )
        for override in invalid_overrides:
            with self.subTest(override=override), self.assertRaises(ValueError):
                CollectionMetadata(**{**values, **override})

        from_list = CollectionMetadata(**{**values, "field_names": ["schlId", "svyYr"]})
        self.assertEqual(from_list.field_names, ("schlId", "svyYr"))

    def test_legacy_manifest_without_source_fields_is_an_integrity_error(self) -> None:
        manifest = self.root / "data/metadata/academyinfo_manifest.jsonl"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "task_id": "legacy",
                    "raw_path": "data/raw/academyinfo/legacy.xml",
                    "sha256": "0" * 64,
                    "bytes": 0,
                    "collected_at": "2026-09-14T12:34:56Z",
                    "http_status": 200,
                    "result_code": "00",
                    "total_count": 0,
                    "row_count": 0,
                    "params": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ManifestIntegrityError, "data_id"):
            self.storage.records()

    def test_lazy_index_parses_each_manifest_record_only_once(self) -> None:
        for index in range(5):
            self.storage.save_bytes(
                f"response-{index}".encode(),
                f"data/raw/academyinfo/{index}.xml",
                self.metadata(f"task-{index}"),
            )
        reopened = AcademyInfoStorage(self.root)

        from unittest.mock import patch

        with patch("academyinfo.storage.json.loads", wraps=json.loads) as loads:
            for index in range(5):
                self.assertIsNotNone(reopened.completed_record(f"task-{index}"))
            self.assertEqual(loads.call_count, 5)

    def test_lazy_index_reads_external_append_as_delta(self) -> None:
        writer = AcademyInfoStorage(self.root)
        reader = AcademyInfoStorage(self.root)
        writer.save_bytes(
            b"first", "data/raw/academyinfo/first.xml", self.metadata("first")
        )
        self.assertIsNotNone(reader.completed_record("first"))
        writer.save_bytes(
            b"second", "data/raw/academyinfo/second.xml", self.metadata("second")
        )

        from unittest.mock import patch

        with patch("academyinfo.storage.json.loads", wraps=json.loads) as loads:
            self.assertIsNotNone(reader.completed_record("second"))
            self.assertEqual(loads.call_count, 1)

    def test_manifest_truncation_is_detected(self) -> None:
        self.storage.save_bytes(
            b"first", "data/raw/academyinfo/first.xml", self.metadata("first")
        )
        self.assertIsNotNone(self.storage.completed_record("first"))
        manifest = self.root / "data/metadata/academyinfo_manifest.jsonl"
        manifest.write_bytes(b"")
        with self.assertRaisesRegex(ManifestIntegrityError, "truncated"):
            self.storage.completed_record("first")

    def test_manifest_inode_replacement_is_detected(self) -> None:
        self.storage.save_bytes(
            b"first", "data/raw/academyinfo/first.xml", self.metadata("first")
        )
        self.assertIsNotNone(self.storage.completed_record("first"))
        manifest = self.root / "data/metadata/academyinfo_manifest.jsonl"
        replacement = manifest.with_suffix(".replacement")
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)

        with self.assertRaisesRegex(ManifestIntegrityError, "replaced"):
            self.storage.completed_record("first")

    def test_trailing_partial_manifest_line_is_truncated_under_lock(self) -> None:
        self.storage.save_bytes(
            b"first", "data/raw/academyinfo/first.xml", self.metadata("first")
        )
        manifest = self.root / "data/metadata/academyinfo_manifest.jsonl"
        valid_bytes = manifest.read_bytes()
        with manifest.open("ab") as stream:
            stream.write(b'{"task_id":"interrupted"')

        records = AcademyInfoStorage(self.root).records()

        self.assertEqual([record.task_id for record in records], ["first"])
        self.assertEqual(manifest.read_bytes(), valid_bytes)

    def test_corrupt_complete_manifest_line_is_not_auto_truncated(self) -> None:
        self.storage.save_bytes(
            b"first", "data/raw/academyinfo/first.xml", self.metadata("first")
        )
        manifest = self.root / "data/metadata/academyinfo_manifest.jsonl"
        with manifest.open("ab") as stream:
            stream.write(b"not-json\n")

        with self.assertRaisesRegex(ManifestIntegrityError, "Invalid manifest line"):
            AcademyInfoStorage(self.root).records()

    def test_concurrent_saves_keep_every_manifest_record(self) -> None:
        errors: list[Exception] = []

        def save(index: int) -> None:
            try:
                self.storage.save_bytes(
                    f"response-{index}".encode(),
                    f"data/raw/academyinfo/concurrent-{index}.xml",
                    self.metadata(f"concurrent-{index}"),
                )
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)

        threads = [threading.Thread(target=save, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.storage.records()), 12)

    def test_paths_cannot_escape_repository_or_raw_root(self) -> None:
        with self.assertRaises(ValueError):
            self.storage.save_bytes(b"x", "data/metadata/x.xml", self.metadata())
        with self.assertRaises(ValueError):
            self.storage.save_bytes(b"x", "../x.xml", self.metadata())


if __name__ == "__main__":
    unittest.main()
