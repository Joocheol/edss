#!/usr/bin/env python3
"""Register an already downloaded immutable raw file in a JSONL manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--reference-years", default="")
    parser.add_argument("--license", default="")
    args = parser.parse_args()

    checksum = digest(args.path)
    if args.manifest.exists():
        for line in args.manifest.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("sha256") == checksum:
                print("already registered")
                return 0
    record = {
        "downloaded_at": datetime.fromtimestamp(args.path.stat().st_mtime, timezone.utc).isoformat(),
        "dataset_id": args.dataset_id,
        "dataset_name": args.dataset,
        "provider": args.provider,
        "source_url": args.source_url,
        "reference_years": args.reference_years,
        "license": args.license,
        "original_filename": args.path.name,
        "relative_path": args.path.as_posix(),
        "bytes": args.path.stat().st_size,
        "sha256": checksum,
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if args.path.suffix.lower() == ".xlsx" else "application/octet-stream",
        "portal_modified": "",
        "portal_reported_rows": "",
        "resolved_download_endpoint": args.source_url,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({"status": "registered", "sha256": checksum, "size_bytes": record["bytes"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
