#!/usr/bin/env python3
"""Reconcile the live EDSS download catalog with immutable local ZIP files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(repo_root: Path, record: dict) -> tuple[bool, str]:
    relative = record.get("local_path", "")
    path = repo_root / relative
    if not relative or not path.is_file():
        return False, "file_missing"
    expected = record.get("sha256", "")
    if expected and sha256(path) != expected:
        return False, "sha256_mismatch"
    try:
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
    except (OSError, zipfile.BadZipFile):
        return False, "invalid_zip"
    if bad_member:
        return False, f"corrupt_member:{bad_member}"
    return True, ""


def build_status(
    targets: list[dict],
    existing_manifest: list[dict],
    attempts: list[dict],
    repo_root: Path,
    verify_files: bool = True,
) -> tuple[list[dict], dict]:
    existing_by_code: dict[str, list[dict]] = defaultdict(list)
    attempt_by_code: dict[str, list[dict]] = defaultdict(list)
    for record in existing_manifest:
        if record.get("status") == "downloaded":
            existing_by_code[str(record.get("domn_code", ""))].append(record)
    for record in attempts:
        attempt_by_code[str(record.get("domn_code", ""))].append(record)

    rows: list[dict] = []
    verified_archives = 0
    invalid_archives = 0
    for target in targets:
        code = str(target["domn_code"])
        new_successes = [row for row in attempt_by_code[code] if row.get("status") == "downloaded"]
        records = new_successes or existing_by_code[code]
        failure = next(
            (row for row in reversed(attempt_by_code[code]) if row.get("status") == "download_failed"),
            None,
        )
        errors: list[str] = []
        if records and verify_files:
            for record in records:
                valid, error = verify_archive(repo_root, record)
                if valid:
                    verified_archives += 1
                else:
                    invalid_archives += 1
                    errors.append(f"{record.get('filename', '')}:{error}")
        if records and not errors:
            status = "downloaded"
        elif records:
            status = "invalid"
        elif failure:
            status = "failed"
            errors.append(f"{failure.get('error_type', 'Error')}:{failure.get('error_message', '')}".rstrip(":"))
        else:
            status = "pending"
        rows.append(
            {
                "source": target.get("source", ""),
                "domn_code": code,
                "dataset": target.get("dataset", ""),
                "advertised_years": target.get("advertised_years", ""),
                "status": status,
                "archive_count": len(records),
                "size_bytes": sum(int(row.get("size_bytes", 0) or 0) for row in records),
                "errors": " | ".join(errors),
            }
        )

    counts = {name: sum(row["status"] == name for row in rows) for name in ("downloaded", "failed", "invalid", "pending")}
    by_source = {}
    for source in sorted({row["source"] for row in rows}):
        source_rows = [row for row in rows if row["source"] == source]
        by_source[source] = {
            "target": len(source_rows),
            **{name: sum(row["status"] == name for row in source_rows) for name in counts},
        }
    logical_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        logical_groups[(row["source"], row["dataset"])].append(row)
    downloaded_logical = sum(all(row["status"] == "downloaded" for row in group) for group in logical_groups.values())
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "grain": "one row per live EDSS domn_code in the three required domains",
        "target_count": len(rows),
        "logical_table_count": len(logical_groups),
        "downloaded_logical_table_count": downloaded_logical,
        **counts,
        "by_source": by_source,
        "verified_archive_count": verified_archives,
        "invalid_archive_count": invalid_archives,
        "complete": counts["downloaded"] == len(rows),
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=Path("data/metadata/edss_live_download_catalog.jsonl"))
    parser.add_argument("--existing-manifest", type=Path, default=Path("data/metadata/edss_file_manifest.jsonl"))
    parser.add_argument("--attempts", type=Path, default=Path("data/metadata/edss_full_collection_attempts.jsonl"))
    parser.add_argument("--status-csv", type=Path, default=Path("data/metadata/edss_full_collection_status.csv"))
    parser.add_argument("--summary", type=Path, default=Path("data/metadata/edss_full_collection_summary.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--skip-file-check", action="store_true")
    args = parser.parse_args()

    rows, summary = build_status(
        read_jsonl(args.targets),
        read_jsonl(args.existing_manifest),
        read_jsonl(args.attempts),
        args.repo_root,
        verify_files=not args.skip_file_check,
    )
    args.status_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.status_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
