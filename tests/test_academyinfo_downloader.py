from __future__ import annotations

import errno
import hashlib
import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from academyinfo.downloader import (  # noqa: E402
    AcademyInfoDownloadError,
    AcademyInfoDownloader,
    AcademyInfoLocalIOError,
    parse_xml_metadata,
)


XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<response xmlns="urn:test"><header><resultCode>00</resultCode>
<resultMsg>OK</resultMsg></header><body><items>
<item><schlId>0000001</schlId></item>
<item><schlId>0000002</schlId></item>
</items><numOfRows>2</numOfRows><totalCount>12</totalCount></body></response>"""


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = io.BytesIO(payload)
        self.status = status
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._payload.read(size)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class AcademyInfoDownloaderTests(unittest.TestCase):
    def test_streams_to_file_and_returns_secret_free_metadata(self) -> None:
        response = FakeResponse(XML)
        captured_request = None

        def fake_urlopen(request, *, timeout):
            nonlocal captured_request
            captured_request = request
            self.assertEqual(timeout, 7)
            return response

        downloader = AcademyInfoDownloader(
            "real+secret", timeout_seconds=7, chunk_size=17
        )
        destination = io.BytesIO()
        params = {"serviceKey": "caller-secret", "svyYr": "2025"}
        with patch("academyinfo.downloader.urlopen", side_effect=fake_urlopen):
            metadata = downloader.download_to_file(
                "https://example.test/api?serviceKey=url-secret",
                params,
                destination,
            )

        self.assertEqual(destination.getvalue(), XML)
        self.assertEqual(metadata.http_status, 200)
        self.assertEqual(metadata.bytes_written, len(XML))
        self.assertEqual(metadata.sha256, hashlib.sha256(XML).hexdigest())
        self.assertEqual(metadata.result_code, "00")
        self.assertEqual(metadata.result_message, "OK")
        self.assertEqual(metadata.total_count, 12)
        self.assertEqual(metadata.item_count, 2)
        self.assertEqual(metadata.field_names, ("schlId",))
        self.assertEqual(metadata.duplicate_row_count, 0)
        self.assertEqual(metadata.attempts, 1)
        self.assertNotIn("service", metadata.__dict__)
        self.assertTrue(all(size == 17 for size in response.read_sizes))

        query = parse_qs(urlsplit(captured_request.full_url).query)
        self.assertEqual(query["serviceKey"], ["real+secret"])
        self.assertNotIn("caller-secret", captured_request.full_url)
        self.assertNotIn("url-secret", captured_request.full_url)
        self.assertEqual(params["serviceKey"], "caller-secret")
        self.assertEqual(captured_request.headers["User-agent"], downloader.user_agent)

    def test_temporary_file_survives_success_for_caller(self) -> None:
        response = FakeResponse(XML)
        downloader = AcademyInfoDownloader("secret")
        with TemporaryDirectory() as directory:
            with patch("academyinfo.downloader.urlopen", return_value=response):
                download = downloader.download_to_temporary_file(
                    "https://example.test/api", {}, directory=Path(directory)
                )
            self.assertTrue(download.path.exists())
            self.assertEqual(download.path.read_bytes(), XML)
            download.path.unlink()

    def test_profiles_field_union_and_exact_duplicates_without_coercing_zeroes(self) -> None:
        payload = b"""<response><header><resultCode>00</resultCode></header><body>
        <items>
          <item><schlId>0000027</schlId><value>01</value></item>
          <item><schlId>0000027</schlId><value>01</value></item>
          <item><schlId>0000027</schlId><value>1</value><note>x</note></item>
        </items><totalCount>3</totalCount></body></response>"""
        downloader = AcademyInfoDownloader("secret")
        with TemporaryDirectory() as directory:
            with patch(
                "academyinfo.downloader.urlopen", return_value=FakeResponse(payload)
            ):
                download = downloader.download_to_temporary_file(
                    "https://example.test/api", {}, directory=Path(directory)
                )

            self.assertEqual(download.metadata.item_count, 3)
            self.assertEqual(download.metadata.field_names, ("note", "schlId", "value"))
            self.assertEqual(download.metadata.duplicate_row_count, 1)
            download.path.unlink()

    def test_retries_with_exponential_backoff_and_removes_failed_files(self) -> None:
        sleeps: list[float] = []
        downloader = AcademyInfoDownloader(
            "secret", attempts=3, backoff_seconds=0.25, sleep=sleeps.append
        )
        transient_http_error = HTTPError(
            "https://example.test/redacted", 503, "busy", {}, None
        )
        outcomes = [transient_http_error, URLError("second"), FakeResponse(XML)]
        with TemporaryDirectory() as directory:
            with patch("academyinfo.downloader.urlopen", side_effect=outcomes):
                download = downloader.download_to_temporary_file(
                    "https://example.test/api", {}, directory=Path(directory)
                )
            self.assertEqual(download.metadata.attempts, 3)
            self.assertEqual(sleeps, [0.25, 0.5])
            self.assertEqual(list(Path(directory).iterdir()), [download.path])

    def test_nonretryable_http_error_is_safe_and_immediate(self) -> None:
        secret = "highly-sensitive-key"
        downloader = AcademyInfoDownloader(secret, attempts=3)
        error = HTTPError(
            f"https://example.test/api?serviceKey={secret}", 401, "bad key", {}, None
        )
        with patch("academyinfo.downloader.urlopen", side_effect=error):
            with self.assertRaises(AcademyInfoDownloadError) as raised:
                downloader.download_to_temporary_file(
                    "https://example.test/api", {"serviceKey": "caller-secret"}
                )
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(raised.exception.http_status, 401)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("caller-secret", str(raised.exception))
        self.assertNotIn("http", str(raised.exception).lower().replace("http 401", ""))

    def test_invalid_xml_is_retried_and_never_copied_to_destination(self) -> None:
        destination = io.BytesIO(b"existing")
        downloader = AcademyInfoDownloader("secret", attempts=2, backoff_seconds=0)
        with patch(
            "academyinfo.downloader.urlopen", return_value=FakeResponse(b"<broken>")
        ):
            with self.assertRaises(AcademyInfoDownloadError) as raised:
                downloader.download_to_file(
                    "https://example.test/api", {}, destination
                )
        self.assertEqual(destination.getvalue(), b"existing")
        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(str(raised.exception).split(": ")[-1], "invalid XML response")

    def test_local_enospc_is_fatal_without_network_retry_or_secret_exposure(self) -> None:
        secret = "highly-sensitive-key"
        sleeps: list[float] = []
        downloader = AcademyInfoDownloader(
            secret, attempts=3, backoff_seconds=0.25, sleep=sleeps.append
        )
        response = FakeResponse(XML)
        with TemporaryDirectory() as directory:
            with (
                patch("academyinfo.downloader.urlopen", return_value=response) as request,
                patch.object(
                    Path,
                    "open",
                    side_effect=OSError(errno.ENOSPC, "disk full", secret),
                ),
            ):
                with self.assertRaises(AcademyInfoLocalIOError) as raised:
                    downloader.download_to_temporary_file(
                        "https://example.test/api", {}, directory=Path(directory)
                    )

            self.assertEqual(request.call_count, 1)
            self.assertEqual(sleeps, [])
            self.assertEqual(raised.exception.errno, errno.ENOSPC)
            self.assertEqual(
                raised.exception.operation, "temporary response file open"
            )
            self.assertNotIn(secret, str(raised.exception))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_streaming_parser_handles_missing_and_invalid_total_count(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "response.xml"
            path.write_bytes(b"<response><item/><totalCount>n/a</totalCount></response>")
            self.assertEqual(parse_xml_metadata(path), (None, None, None, 1))


if __name__ == "__main__":
    unittest.main()
