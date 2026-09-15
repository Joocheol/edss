"""Discover and download immutable EDSS open-data archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from sourcefiles import (
    CollectionError,
    HttpSession,
    Manifest,
    append_jsonl,
    clean_component,
    store_local_file,
    store_response,
)


BASE = "https://www.edmgr.kr/edss/es/opd/odd/od"
LANDING = f"{BASE}/es_opd_oddod01_001"
CATALOG = f"{BASE}/es_opd_oddod01_007"
FILE_LIST = f"{BASE}/es_opd_oddod01_004"
DOWNLOAD = f"{BASE}/es_opd_oddod01_005"
DEFAULT_CONFIG = Path("config/edss_download.json")


@dataclass(frozen=True)
class EdssDataset:
    source: str
    major_area: str
    dataset: str
    domn_code: str
    advertised_years: str
    fields: str


@dataclass(frozen=True)
class EdssTask:
    dataset: EdssDataset
    file_year: str
    attachment_serial: str

    @property
    def task_id(self) -> str:
        payload = {
            "source": self.dataset.source,
            "domn_code": self.dataset.domn_code,
            "file_year": self.file_year,
            "attachment_serial": self.attachment_serial,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "edss-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config.get("sources"), dict) or not config["sources"]:
        raise CollectionError("EDSS config must define non-empty sources")
    return config


def discover_catalog(session: HttpSession) -> list[EdssDataset]:
    session.get_text(LANDING)
    payload = {
        "searchDomnNm": "",
        "searchPvsnArtclNm": "",
        "searchPvsnYr": "",
        "currentArtcClssCd": "",
        "currentPvsnArtclCd": "",
        "searchEduDataSeNm": "",
        "searchAllTabYn": "Y",
    }
    response = session.post_json(CATALOG, payload, referer=LANDING)
    if not isinstance(response, dict) or not isinstance(response.get("aplList"), list):
        raise CollectionError("EDSS catalog response has no aplList")
    datasets: list[EdssDataset] = []
    for row in response["aplList"]:
        if not isinstance(row, dict) or not row.get("domnCd"):
            continue
        datasets.append(
            EdssDataset(
                source=str(row.get("eduDataSeNm") or ""),
                major_area=str(row.get("ldomnNm") or ""),
                dataset=str(row.get("domnNmOri") or row.get("domnNm") or ""),
                domn_code=str(row["domnCd"]),
                advertised_years=str(row.get("pvsnYrNm") or "").replace("\r\n", " "),
                fields=str(row.get("pvsnArtclNmOri") or ""),
            )
        )
    return datasets


def select_scope(
    datasets: Iterable[EdssDataset],
    expected: Mapping[str, int],
    *,
    sources: set[str] | None = None,
    domn_codes: set[str] | None = None,
    verify_counts: bool = True,
) -> list[EdssDataset]:
    selected_sources = sources or set(expected)
    unknown = selected_sources - set(expected)
    if unknown:
        raise CollectionError(f"unknown EDSS sources: {sorted(unknown)}")
    in_scope = [row for row in datasets if row.source in selected_sources]
    if verify_counts and not domn_codes:
        actual = {source: sum(row.source == source for row in in_scope) for source in selected_sources}
        wanted = {source: int(expected[source]) for source in selected_sources}
        if actual != wanted:
            raise CollectionError(f"EDSS catalog count changed: expected={wanted}, actual={actual}")
    if domn_codes:
        in_scope = [row for row in in_scope if row.domn_code in domn_codes]
        found = {row.domn_code for row in in_scope}
        if found != domn_codes:
            raise CollectionError(f"EDSS domnCd not found in selected scope: {sorted(domn_codes - found)}")
    return sorted(in_scope, key=lambda row: (row.source, row.domn_code, row.dataset))


def fetch_file_list(session: HttpSession, domn_code: str) -> list[dict]:
    response = session.post_json(FILE_LIST, {"domnCd": domn_code}, referer=LANDING)
    if not isinstance(response, list):
        raise CollectionError(f"EDSS file-list response is not a list: domnCd={domn_code}")
    return [row for row in response if isinstance(row, dict)]


def select_task(dataset: EdssDataset, available: list[dict], file_year: str) -> EdssTask:
    target = next((row for row in available if str(row.get("atflYr")) == file_year), None)
    if target is None:
        years = [str(row.get("atflYr")) for row in available]
        raise CollectionError(
            f"requested file year is unavailable: domnCd={dataset.domn_code} "
            f"year={file_year} available={years}"
        )
    serial = str(target.get("atchFileSn") or "")
    if not serial:
        raise CollectionError(f"EDSS file entry has no atchFileSn: domnCd={dataset.domn_code}")
    return EdssTask(dataset, file_year, serial)


def encode_download_form(task: EdssTask) -> bytes:
    """Match the complete form serialized by the EDSS download page."""
    return urllib.parse.urlencode(
        {
            "currentArtcClssCd": "",
            "currentPvsnArtclCd": "",
            "searchEduDataSeNm": "",
            "atchFileSn": task.attachment_serial,
            "domnCd": task.dataset.domn_code,
            "atflYr": task.file_year,
            "ldomnNm": task.dataset.major_area,
            "searchDomnNm": "",
            "searchPvsnArtclNm": "",
            "searchPvsnYr": "",
            "pageIndex": "",
        }
    ).encode()


def download_task(session: HttpSession, task: EdssTask, raw_root: Path):
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": LANDING,
    }
    with session.open(
        DOWNLOAD,
        data=encode_download_form(task),
        headers=headers,
        method="POST",
    ) as response:
        return store_response(
            response,
            task_destination(task, raw_root),
            archive_kind="zip",
            fallback_filename=f"{task.dataset.domn_code}_{task.file_year}.zip",
        )


def task_destination(task: EdssTask, raw_root: Path) -> Path:
    return (
        raw_root
        / clean_component(task.dataset.source)
        / f"{task.dataset.domn_code}_{clean_component(task.dataset.dataset)}"
        / clean_component(task.file_year)
    )


class EdssBrowser:
    """Drive the provider's own JavaScript download function in Chrome."""

    def __init__(self, *, timeout: float, headed: bool) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CollectionError(
                "browser transport requires Playwright; run with "
                "`uv run --with playwright python scripts/download_edss.py ...`"
            ) from exc
        self._manager = sync_playwright().start()
        try:
            self._browser = self._manager.chromium.launch(channel="chrome", headless=not headed)
            self._context = self._browser.new_context(accept_downloads=True)
            self._page = self._context.new_page()
            self._page.set_default_timeout(timeout * 1000)
            self._page.goto(LANDING, wait_until="domcontentloaded", timeout=timeout * 1000)
            self._page.wait_for_function("typeof fileDownLoad === 'function'")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for attribute in ("_context", "_browser"):
            resource = getattr(self, attribute, None)
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
                setattr(self, attribute, None)
        manager = getattr(self, "_manager", None)
        if manager is not None:
            manager.stop()
            self._manager = None

    def download(self, task: EdssTask, raw_root: Path):
        arguments = [
            task.attachment_serial,
            task.dataset.domn_code,
            task.file_year,
            task.dataset.major_area,
        ]
        with self._page.expect_download() as pending:
            self._page.evaluate("args => fileDownLoad(...args)", arguments)
        download = pending.value
        failure = download.failure()
        if failure:
            raise CollectionError(f"EDSS browser download failed: {failure}")
        with tempfile.TemporaryDirectory(prefix="edss-download-") as temporary:
            filename = download.suggested_filename or f"{task.dataset.domn_code}_{task.file_year}.zip"
            staged = Path(temporary) / clean_component(filename)
            download.save_as(staged)
            return store_local_file(
                staged,
                task_destination(task, raw_root),
                archive_kind="zip",
                filename=filename,
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def build_record(task: EdssTask, stored, *, transport: str) -> dict:
    return {
        "task_id": task.task_id,
        "status": "downloaded",
        "downloaded_at": utc_now(),
        "provider": "교육부·한국교육학술정보원 EDSS",
        "source": task.dataset.source,
        "major_area": task.dataset.major_area,
        "dataset": task.dataset.dataset,
        "domn_code": task.dataset.domn_code,
        "advertised_years": task.dataset.advertised_years,
        "file_year": task.file_year,
        "attachment_serial": task.attachment_serial,
        "download_method": transport,
        "source_url": DOWNLOAD,
        "landing_url": LANDING,
        "filename": stored.filename,
        "local_path": stored.path.as_posix(),
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/edss"))
    parser.add_argument("--manifest", type=Path, default=Path("data/metadata/edss_manifest.jsonl"))
    parser.add_argument("--attempts", type=Path, default=Path("data/metadata/edss_attempts.jsonl"))
    parser.add_argument("--log", type=Path, default=Path("logs/edss-download.log"))
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--domn-code", action="append", default=[])
    parser.add_argument("--year", help="EDSS file-list year; defaults to config value")
    parser.add_argument("--catalog-only", action="store_true", help="print the selected live catalog without downloads")
    parser.add_argument("--plan-only", action="store_true", help="resolve file-list entries and print tasks without downloads")
    parser.add_argument("--limit", type=int, help="download at most N selected datasets")
    parser.add_argument(
        "--transport",
        choices=("browser", "http"),
        default="browser",
        help="browser is required by the current EDSS download endpoint",
    )
    parser.add_argument("--headed", action="store_true", help="show Chrome when using browser transport")
    parser.add_argument("--delay", type=float, default=1.0, help="polite delay between datasets")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    if args.delay < 0:
        raise SystemExit("--delay must be non-negative")
    config = load_config(args.config)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    log = logging.getLogger("edss.download")
    session = HttpSession(
        user_agent="edss-research-collector/2.0",
        retries=args.retries,
        retry_delay=max(args.delay, 0.1),
        timeout=args.timeout,
    )
    try:
        catalog = discover_catalog(session)
        expected_sources = {str(key): int(value) for key, value in config["sources"].items()}
        full_scope = select_scope(catalog, expected_sources)
        expected_total = int(config.get("expected_physical_download_units", 0))
        if expected_total and len(full_scope) != expected_total:
            raise CollectionError(
                f"EDSS total catalog count changed: expected={expected_total} actual={len(full_scope)}"
            )
        selected = select_scope(
            catalog,
            expected_sources,
            sources=set(args.source) or None,
            domn_codes=set(args.domn_code) or None,
        )
    except Exception as exc:
        log.error("catalog failed: %s: %s", type(exc).__name__, exc)
        return 1
    if args.catalog_only:
        for row in selected:
            print(json.dumps(row.__dict__, ensure_ascii=False, sort_keys=True))
        log.info("selected %d EDSS physical download units", len(selected))
        return 0
    if args.limit is not None:
        selected = selected[: args.limit]
    file_year = args.year or str(config.get("default_file_year", "ALL"))
    if args.plan_only:
        failures = 0
        for dataset in selected:
            try:
                task = select_task(dataset, fetch_file_list(session, dataset.domn_code), file_year)
                print(
                    json.dumps(
                        {
                            "task_id": task.task_id,
                            "source": dataset.source,
                            "dataset": dataset.dataset,
                            "domn_code": dataset.domn_code,
                            "file_year": task.file_year,
                            "attachment_serial": task.attachment_serial,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            except Exception as exc:
                failures += 1
                log.error("plan failed domnCd=%s: %s: %s", dataset.domn_code, type(exc).__name__, exc)
        log.info("planned %d EDSS downloads with %d failures", len(selected), failures)
        return 1 if failures else 0
    manifest = Manifest(args.manifest)
    failures = stored_count = skipped = 0
    browser = None
    try:
        if args.transport == "browser":
            browser = EdssBrowser(timeout=args.timeout, headed=args.headed)
        for index, dataset in enumerate(selected, start=1):
            try:
                task = select_task(dataset, fetch_file_list(session, dataset.domn_code), file_year)
                if manifest.verified(task.task_id):
                    skipped += 1
                    log.info("[%d/%d] skipped domnCd=%s %s", index, len(selected), dataset.domn_code, dataset.dataset)
                else:
                    stored = (
                        browser.download(task, args.raw_root)
                        if browser is not None
                        else download_task(session, task, args.raw_root)
                    )
                    manifest.add(build_record(task, stored, transport=args.transport))
                    stored_count += 1
                    log.info(
                        "[%d/%d] stored domnCd=%s bytes=%d %s",
                        index,
                        len(selected),
                        dataset.domn_code,
                        stored.size_bytes,
                        stored.filename,
                    )
            except Exception as exc:
                failures += 1
                append_jsonl(
                    args.attempts,
                    {
                        "attempted_at": utc_now(),
                        "source": dataset.source,
                        "dataset": dataset.dataset,
                        "domn_code": dataset.domn_code,
                        "file_year": file_year,
                        "transport": args.transport,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                log.error("[%d/%d] failed domnCd=%s: %s: %s", index, len(selected), dataset.domn_code, type(exc).__name__, exc)
            if index < len(selected) and args.delay:
                time.sleep(args.delay)
    except Exception as exc:
        log.error("browser setup failed: %s: %s", type(exc).__name__, exc)
        return 1
    finally:
        if browser is not None:
            browser.close()
    log.info(
        "summary planned=%d stored=%d skipped=%d failed=%d",
        len(selected),
        stored_count,
        skipped,
        failures,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
