#!/usr/bin/env python3
"""Idempotently download public file datasets and record provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PORTAL = "https://www.data.go.kr"
RESOLVE_URL = f"{PORTAL}/tcs/dss/selectFileDataDownload.do"
DOWNLOAD_URL = f"{PORTAL}/cmm/cmm/fileDownload.do"
USER_AGENT = "edss-panel-collector/0.1 (+public research reproducibility)"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_component(value: str, fallback: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value).strip(" .")
    return value[:180] or fallback


def request_bytes(request: urllib.request.Request, retries: int, timeout: int) -> tuple[bytes, dict[str, str]]:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), dict(response.headers.items())
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt >= retries:
                raise
            logging.warning("temporary download error (%s); retrying", type(exc).__name__)
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def resolve_download(dataset: dict, retries: int, timeout: int) -> dict:
    form = urllib.parse.urlencode({
        "publicDataPk": dataset["id"],
        "publicDataDetailPk": dataset["detail_pk"],
        "publicDataTyCode": "PR0051",
    }).encode("ascii")
    request = urllib.request.Request(
        RESOLVE_URL,
        data=form,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    body, _ = request_bytes(request, retries, timeout)
    payload = json.loads(body.decode("utf-8"))
    if payload.get("status") is not True:
        raise RuntimeError(payload.get("error") or payload.get("errorDc") or "portal did not resolve a file")
    registration = payload.get("fileDataRegistVO") or {}
    detail = payload.get("dataSetFileDetailInfo") or {}
    return {
        "attachment_id": payload["atchFileId"],
        "file_detail_sequence": str(payload["fileDetailSn"]),
        "data_name": registration.get("dataNm") or detail.get("dataNm") or dataset["name"],
        "original_name": registration.get("orginlFileNm") or "",
        "extension": (registration.get("atchFileExtsn") or dataset.get("format", "bin")).lower(),
        "portal_modified": detail.get("updtDt") or dataset.get("modified", ""),
        "portal_rows": registration.get("atchFileCo") or detail.get("atchFileCo") or "",
    }


def final_download_url(resolved: dict) -> str:
    query = urllib.parse.urlencode({
        "atchFileId": resolved["attachment_id"],
        "fileDetailSn": resolved["file_detail_sequence"],
        "dataNm": resolved["data_name"],
    })
    return f"{DOWNLOAD_URL}?{query}"


def validate_payload(path: Path, extension: str) -> None:
    head = path.read_bytes()[:512].lstrip()
    if head.lower().startswith((b"<!doctype html", b"<html")):
        raise RuntimeError("download returned HTML instead of a data file")
    if extension == "xlsx" and not head.startswith(b"PK"):
        raise RuntimeError("XLSX download is not a ZIP-based workbook")
    if extension == "hwp" and not head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise RuntimeError("HWP download does not have the expected OLE signature")


def latest_manifest(path: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                records[record["dataset_id"]] = record
    return records


def append_manifest(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def download_dataset(dataset: dict, raw_root: Path, manifest_path: Path, known: dict[str, dict], retries: int, timeout: int) -> dict:
    previous = known.get(dataset["id"])
    if previous:
        previous_path = Path(previous["relative_path"])
        if not previous_path.is_absolute():
            previous_path = Path.cwd() / previous_path
        if previous_path.exists() and sha256(previous_path) == previous["sha256"]:
            logging.info("skip verified existing dataset %s", dataset["id"])
            return {"id": dataset["id"], "status": "skipped_verified", "path": str(previous_path)}

    resolved = resolve_download(dataset, retries, timeout)
    extension = resolved["extension"].lstrip(".")
    original = resolved["original_name"] or f"{resolved['data_name']}.{extension}"
    actual_extension = Path(original).suffix.lower().lstrip(".") or extension
    dataset_dir = raw_root / safe_component(dataset["provider"], "provider") / f"{dataset['id']}_{safe_component(dataset['name'], 'dataset')}"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    output = dataset_dir / safe_component(original, f"{dataset['id']}.{extension}")
    partial = output.with_suffix(output.suffix + ".part")

    headers: dict[str, str] = {}
    if partial.exists() and partial.stat().st_size > 0:
        logging.info("resuming from complete partial file for dataset %s", dataset["id"])
        validate_payload(partial, actual_extension)
    else:
        request = urllib.request.Request(final_download_url(resolved), headers={"User-Agent": USER_AGENT})
        body, headers = request_bytes(request, retries, timeout)
        partial.write_bytes(body)
        validate_payload(partial, actual_extension)
    os.replace(partial, output)
    digest = sha256(output)
    try:
        relative_path = output.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        relative_path = output.resolve()
    record = {
        "dataset_id": dataset["id"],
        "dataset_name": dataset["name"],
        "provider": dataset["provider"],
        "source_url": dataset["source_url"],
        "resolved_download_endpoint": DOWNLOAD_URL,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "relative_path": relative_path.as_posix(),
        "original_filename": original,
        "bytes": output.stat().st_size,
        "sha256": digest,
        "content_type": headers.get("Content-Type", ""),
        "reference_years": dataset.get("reference_years", ""),
        "license": dataset.get("license", ""),
        "portal_modified": resolved["portal_modified"],
        "portal_reported_rows": resolved["portal_rows"],
    }
    append_manifest(manifest_path, record)
    known[dataset["id"]] = record
    logging.info("downloaded dataset %s (%d bytes)", dataset["id"], record["bytes"])
    return {"id": dataset["id"], "status": "downloaded", "path": str(output), "sha256": digest, "bytes": record["bytes"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/file_datasets.json"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/public_data_portal"))
    parser.add_argument("--manifest", type=Path, default=Path("data/metadata/file_manifest.jsonl"))
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--log", type=Path, default=Path("logs/file_collection.log"))
    args = parser.parse_args()

    args.log.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(args.log, encoding="utf-8")],
    )
    datasets = json.loads(args.config.read_text(encoding="utf-8"))
    selected = set(args.ids or [])
    known = latest_manifest(args.manifest)
    outcomes = []
    for dataset in datasets:
        if selected and dataset["id"] not in selected:
            continue
        if dataset["delivery"] != "portal_file":
            outcomes.append({"id": dataset["id"], "status": "provider_link_only", "url": dataset.get("external_url", "")})
            logging.info("provider link requires source-specific handling: %s", dataset["id"])
            continue
        try:
            outcomes.append(download_dataset(dataset, args.raw_root, args.manifest, known, args.retries, args.timeout))
        except Exception as exc:  # keep collecting independent datasets
            logging.error("dataset %s failed: %s", dataset["id"], exc)
            outcomes.append({"id": dataset["id"], "status": "failed", "error": str(exc)})
    print(json.dumps(outcomes, ensure_ascii=False, indent=2))
    if any(item["status"] == "failed" for item in outcomes):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
