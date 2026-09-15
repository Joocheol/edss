import io
import json
import sys
import tempfile
import unittest
import zipfile
from email.message import Message
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from sourcefiles.core import (
    IntegrityError,
    Manifest,
    clean_component,
    parse_content_disposition,
    sha256_file,
    store_local_file,
    store_response,
)


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, *, content_type: str, disposition: str = ""):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if disposition:
            self.headers["Content-Disposition"] = disposition


def make_xlsx() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return target.getvalue()


class SourceFileCoreTests(unittest.TestCase):
    def test_filename_and_component_sanitizing(self):
        headers = Message()
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''%EB%8C%80%ED%95%99.xlsx"
        self.assertEqual(parse_content_disposition(headers, "fallback.bin"), "대학.xlsx")
        headers.replace_header(
            "Content-Disposition",
            'attachment; filename="2025%EB%85%84%20%EB%8C%80%ED%95%99.xlsx"',
        )
        self.assertEqual(parse_content_disposition(headers, "fallback.bin"), "2025년 대학.xlsx")
        self.assertEqual(clean_component("대학/학과:현황"), "대학_학과_현황")
        long_name = clean_component("긴파일명" * 100 + ".xlsx")
        self.assertLessEqual(len(long_name.encode("utf-8")), 240)
        self.assertTrue(long_name.endswith(".xlsx"))

    def test_store_response_validates_and_publishes_xlsx(self):
        with tempfile.TemporaryDirectory() as directory:
            response = FakeResponse(
                make_xlsx(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                disposition='attachment; filename="sample.xlsx"',
            )
            stored = store_response(
                response,
                Path(directory),
                archive_kind="xlsx",
                fallback_filename="fallback.xlsx",
            )
            self.assertEqual(stored.filename, "sample.xlsx")
            self.assertEqual(stored.sha256, sha256_file(stored.path))
            self.assertFalse((Path(directory) / ".sample.xlsx.part").exists())

    def test_store_response_rejects_html(self):
        with tempfile.TemporaryDirectory() as directory:
            response = FakeResponse(b"<!doctype html><title>error</title>", content_type="text/html")
            with self.assertRaises(IntegrityError):
                store_response(
                    response,
                    Path(directory),
                    archive_kind="zip",
                    fallback_filename="error.zip",
                )

    def test_store_local_file_validates_browser_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.xlsx"
            staged.write_bytes(make_xlsx())
            stored = store_local_file(
                staged,
                root / "published",
                archive_kind="xlsx",
                filename="학교별.xlsx",
            )
            self.assertEqual(stored.filename, "학교별.xlsx")
            self.assertEqual(stored.sha256, sha256_file(stored.path))
            self.assertEqual(stored.path.read_bytes(), staged.read_bytes())

    def test_manifest_verifies_checksum_before_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "file.bin"
            data.write_bytes(b"original")
            record = {
                "task_id": "task-1",
                "status": "downloaded",
                "local_path": data.as_posix(),
                "size_bytes": data.stat().st_size,
                "sha256": sha256_file(data),
            }
            manifest_path = root / "manifest.jsonl"
            manifest_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            manifest = Manifest(manifest_path)
            self.assertTrue(manifest.verified("task-1"))
            data.write_bytes(b"modified")
            with self.assertRaises(IntegrityError):
                manifest.verified("task-1")


if __name__ == "__main__":
    unittest.main()
