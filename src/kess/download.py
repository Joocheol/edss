"""Discover and download KESS public higher-education Excel datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Mapping

from sourcefiles import CollectionError, HttpSession, Manifest, append_jsonl, clean_component, store_response


LANDING = "https://kess.kedi.re.kr/contents/dataset?itemCode=04&menuId=m_02_04_03_02&tabId=m2"
POLL = "https://kess.kedi.re.kr/contents/dataset/poll"
DOWNLOAD = "https://kess.kedi.re.kr/contents/dataSet/downLoad.do"
DEFAULT_CONFIG = Path("config/kess_download.json")
CALL_RE = re.compile(
    r"downLoad\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*\)"
)


@dataclass(frozen=True)
class RawKessFile:
    label: str
    file_id: str
    remote_name: str
    display_name: str
    group: str
    year: int


@dataclass(frozen=True)
class KessTask:
    domain: str
    series: str
    series_label: str
    file: RawKessFile

    @property
    def task_id(self) -> str:
        payload = {
            "group": self.file.group,
            "series": self.series,
            "year": self.file.year,
            "file_id": self.file.file_id,
            "remote_name": self.file.remote_name,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "kess-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().rstrip(":").strip()


class _KessCatalogParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.li_stack: list[dict[str, list]] = []
        self.rows: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "li":
            self.li_stack.append({"texts": [], "calls": []})
            return
        if not self.li_stack:
            return
        attributes = dict(attrs)
        onclick = attributes.get("onclick") or ""
        if CALL_RE.search(onclick):
            self.li_stack[-1]["calls"].append(onclick)

    def handle_data(self, data: str) -> None:
        if self.li_stack and data.strip():
            self.li_stack[-1]["texts"].append(data.strip())

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "li" or not self.li_stack:
            return
        bucket = self.li_stack.pop()
        if not bucket["calls"]:
            return
        label = next(
            (text for text in bucket["texts"] if not re.fullmatch(r"20\d{2}", text)),
            "",
        )
        for call in bucket["calls"]:
            self.rows.append((normalize_label(label), call))


def parse_catalog(html: str) -> list[RawKessFile]:
    parser = _KessCatalogParser()
    parser.feed(html)
    files: list[RawKessFile] = []
    for label, call in parser.rows:
        match = CALL_RE.search(call)
        if not match:
            continue
        file_id, remote_name, display_name, group = match.groups()
        year_match = re.match(r"(20\d{2})", display_name)
        short_year_match = re.match(r"(\d{2})년", display_name)
        if year_match:
            year = int(year_match.group(1))
        elif short_year_match:
            year = 2000 + int(short_year_match.group(1))
        else:
            raise CollectionError(f"KESS filename has no leading year: {display_name}")
        files.append(
            RawKessFile(
                label=label,
                file_id=file_id,
                remote_name=remote_name,
                display_name=display_name,
                group=group,
                year=year,
            )
        )
    return files


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config.get("groups"), dict) or not config["groups"]:
        raise CollectionError("KESS config must define groups")
    return config


def discover_catalog(session: HttpSession, landing_url: str = LANDING) -> list[RawKessFile]:
    return parse_catalog(session.get_text(landing_url))


def build_tasks(files: Iterable[RawKessFile], groups: Mapping[str, dict]) -> list[KessTask]:
    tasks: list[KessTask] = []
    counts: dict[tuple[str, str], int] = {}
    seen: set[tuple[str, str, int, str]] = set()
    for file in files:
        group = groups.get(file.group)
        if not group:
            continue
        series_config = group.get("series", {}).get(file.label)
        if not series_config:
            raise CollectionError(f"unmapped KESS series label: group={file.group} label={file.label!r}")
        key = (file.group, file.label, file.year, file.remote_name)
        if key in seen:
            raise CollectionError(f"duplicate KESS catalog file: {key}")
        seen.add(key)
        tasks.append(
            KessTask(
                domain=str(group["domain"]),
                series=str(series_config["id"]),
                series_label=file.label,
                file=file,
            )
        )
        count_key = (file.group, file.label)
        counts[count_key] = counts.get(count_key, 0) + 1
    for group_id, group in groups.items():
        for label, series in group.get("series", {}).items():
            expected = int(series["expected_files"])
            actual = counts.get((group_id, label), 0)
            if actual != expected:
                raise CollectionError(
                    f"KESS catalog count changed: group={group_id} label={label} "
                    f"expected={expected} actual={actual}"
                )
    return sorted(tasks, key=lambda task: (task.domain, task.series, -task.file.year))


def filter_tasks(
    tasks: Iterable[KessTask],
    *,
    domains: set[str] | None = None,
    series: set[str] | None = None,
    years: set[int] | None = None,
) -> list[KessTask]:
    available = list(tasks)
    checks = (
        ("domain", domains, {task.domain for task in available}),
        ("series", series, {task.series for task in available}),
        ("year", years, {task.file.year for task in available}),
    )
    for label, requested, known in checks:
        unknown = (requested or set()) - known
        if unknown:
            raise CollectionError(f"unknown KESS {label} filter: {sorted(unknown)}")
    selected = [
        task
        for task in available
        if (not domains or task.domain in domains)
        and (not series or task.series in series)
        and (not years or task.file.year in years)
    ]
    if not selected:
        raise CollectionError("KESS filters selected no files")
    return selected


def encode_multipart(fields: Mapping[str, str]) -> tuple[bytes, str]:
    boundary = "----edss-collector-" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def submit_usage_poll(session: HttpSession, task: KessTask, affiliation: str, purpose: str) -> None:
    body, content_type = encode_multipart(
        {
            "TYPE_A": affiliation,
            "TYPE_B": purpose,
            "FILE_ID": task.file.file_id,
            "GROUP_A": task.file.group,
        }
    )
    with session.open(
        POLL,
        data=body,
        headers={"Content-Type": content_type, "Referer": LANDING},
        method="POST",
    ) as response:
        response.read()


def download_task(session: HttpSession, task: KessTask, raw_root: Path):
    query = urllib.parse.urlencode(
        {"fileNm": task.file.remote_name, "userfileNm": task.file.display_name}
    )
    url = f"{DOWNLOAD}?{query}"
    version = f"{task.file.file_id}_{Path(task.file.remote_name).stem}"
    destination = (
        raw_root
        / clean_component(task.domain)
        / clean_component(task.series)
        / str(task.file.year)
        / clean_component(version)
    )
    with session.open(url, headers={"Referer": LANDING}) as response:
        return store_response(
            response,
            destination,
            archive_kind="xlsx",
            fallback_filename=task.file.display_name,
        )


def build_record(task: KessTask, stored) -> dict:
    query = urllib.parse.urlencode(
        {"fileNm": task.file.remote_name, "userfileNm": task.file.display_name}
    )
    return {
        "task_id": task.task_id,
        "status": "downloaded",
        "downloaded_at": utc_now(),
        "provider": "한국교육개발원 교육통계서비스(KESS)",
        "domain": task.domain,
        "series": task.series,
        "series_label": task.series_label,
        "display_year": task.file.year,
        "group": task.file.group,
        "file_id": task.file.file_id,
        "remote_name": task.file.remote_name,
        "original_filename": task.file.display_name,
        "download_method": "usage_poll_http",
        "source_url": f"{DOWNLOAD}?{query}",
        "landing_url": LANDING,
        "filename": stored.filename,
        "local_path": stored.path.as_posix(),
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
    }


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/kess"))
    parser.add_argument("--manifest", type=Path, default=Path("data/metadata/kess_manifest.jsonl"))
    parser.add_argument("--attempts", type=Path, default=Path("data/metadata/kess_attempts.jsonl"))
    parser.add_argument("--log", type=Path, default=Path("logs/kess-download.log"))
    parser.add_argument("--affiliation", help="affiliation key from config; required for downloads")
    parser.add_argument("--purpose", help="purpose key from config; required for downloads")
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--series", action="append", default=[])
    parser.add_argument("--year", action="append", type=int, default=[])
    parser.add_argument("--catalog-only", action="store_true", help="print the verified 146-file catalog")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=1.0)
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
    if not args.catalog_only:
        if args.affiliation not in config.get("affiliations", {}):
            choices = ", ".join(config.get("affiliations", {}))
            raise SystemExit(f"--affiliation is required; choose one of: {choices}")
        if args.purpose not in config.get("purposes", {}):
            choices = ", ".join(config.get("purposes", {}))
            raise SystemExit(f"--purpose is required; choose one of: {choices}")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(args.log, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    log = logging.getLogger("kess.download")
    session = HttpSession(
        user_agent="edss-research-collector/2.0",
        retries=args.retries,
        retry_delay=max(args.delay, 0.1),
        timeout=args.timeout,
    )
    try:
        tasks = build_tasks(discover_catalog(session, config.get("landing_url", LANDING)), config["groups"])
        expected = int(config.get("expected_physical_year_files", 0))
        if expected and len(tasks) != expected:
            raise CollectionError(f"KESS total catalog count changed: expected={expected} actual={len(tasks)}")
    except Exception as exc:
        log.error("catalog failed: %s: %s", type(exc).__name__, exc)
        return 1
    try:
        tasks = filter_tasks(
            tasks,
            domains=set(args.domain) or None,
            series=set(args.series) or None,
            years=set(args.year) or None,
        )
    except Exception as exc:
        log.error("filter failed: %s: %s", type(exc).__name__, exc)
        return 1
    if args.catalog_only:
        for task in tasks:
            print(
                json.dumps(
                    {
                        "task_id": task.task_id,
                        "domain": task.domain,
                        "series": task.series,
                        **task.file.__dict__,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        log.info("selected %d KESS physical year files", len(tasks))
        return 0
    if args.limit is not None:
        tasks = tasks[: args.limit]
    affiliation = str(config["affiliations"][args.affiliation])
    purpose = str(config["purposes"][args.purpose])
    manifest = Manifest(args.manifest)
    failures = stored_count = skipped = 0
    for index, task in enumerate(tasks, start=1):
        try:
            if manifest.verified(task.task_id):
                skipped += 1
                log.info("[%d/%d] skipped %s %s", index, len(tasks), task.series, task.file.year)
            else:
                submit_usage_poll(session, task, affiliation, purpose)
                stored = download_task(session, task, args.raw_root)
                manifest.add(build_record(task, stored))
                stored_count += 1
                log.info(
                    "[%d/%d] stored %s %d bytes=%d %s",
                    index,
                    len(tasks),
                    task.series,
                    task.file.year,
                    stored.size_bytes,
                    stored.filename,
                )
        except Exception as exc:
            failures += 1
            append_jsonl(
                args.attempts,
                {
                    "attempted_at": utc_now(),
                    "domain": task.domain,
                    "series": task.series,
                    "display_year": task.file.year,
                    "file_id": task.file.file_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            log.error("[%d/%d] failed %s %d: %s: %s", index, len(tasks), task.series, task.file.year, type(exc).__name__, exc)
        if index < len(tasks) and args.delay:
            time.sleep(args.delay)
    log.info(
        "summary planned=%d stored=%d skipped=%d failed=%d",
        len(tasks),
        stored_count,
        skipped,
        failures,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
