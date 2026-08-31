#!/usr/bin/env python3
"""Inspect an EDSS ZIP without extracting or modifying its CSV members."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recover_name(value: str) -> str:
    try:
        value = value.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return unicodedata.normalize("NFC", value)


def decode_csv(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", data, 0, 1, "unsupported CSV encoding")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--catalog-code", required=True)
    parser.add_argument("--domn-code", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--original-filename", default="")
    args = parser.parse_args()

    members = []
    header_variants: set[tuple[str, ...]] = set()
    total_rows = 0
    with zipfile.ZipFile(args.archive) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"corrupt ZIP member: {recover_name(bad_member)}")
        for info in archive.infolist():
            text, encoding = decode_csv(archive.read(info))
            rows = list(csv.reader(io.StringIO(text)))
            header = tuple(rows[0]) if rows else tuple()
            header_variants.add(header)
            data_rows = rows[1:]
            years = sorted({row[0] for row in data_rows if row})
            total_rows += len(data_rows)
            members.append(
                {
                    "filename": recover_name(info.filename),
                    "compressed_bytes": info.compress_size,
                    "uncompressed_bytes": info.file_size,
                    "encoding": encoding,
                    "row_count": len(data_rows),
                    "column_count": len(header),
                    "observed_years": years,
                }
            )

    if len(header_variants) != 1:
        raise RuntimeError(f"inconsistent CSV headers: {len(header_variants)} variants")
    header = list(next(iter(header_variants), tuple()))
    result = {
        "inspected_at": datetime.now(timezone.utc).isoformat(),
        "provider": "교육부·한국교육학술정보원 EDSS",
        "source_url": args.source_url,
        "dataset": args.dataset,
        "catalog_code": args.catalog_code,
        "domn_code": args.domn_code,
        "archive_filename": args.archive.name,
        "archive_path": args.archive.as_posix(),
        "archive_bytes": args.archive.stat().st_size,
        "archive_sha256": checksum(args.archive),
        "member_count": len(members),
        "total_rows": total_rows,
        "header_variant_count": len(header_variants),
        "original_fields": header,
        "field_definition_status": "원본 필드명만 확인; 자료형·단위·결측값 의미는 별도 설명서 확인 필요",
        "members": members,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.manifest:
        manifest_record = {
            "downloaded_at": datetime.fromtimestamp(args.archive.stat().st_mtime, timezone.utc).isoformat(),
            "provider": result["provider"],
            "source_url": args.source_url,
            "dataset": args.dataset,
            "catalog_code": args.catalog_code,
            "domn_code": args.domn_code,
            "file_year": "ALL",
            "advertised_years": "2009~2025",
            "filename": args.archive.name,
            "original_filename": unicodedata.normalize("NFC", args.original_filename) if args.original_filename else args.archive.name,
            "local_path": args.archive.as_posix(),
            "size_bytes": args.archive.stat().st_size,
            "sha256": result["archive_sha256"],
            "license": "EDSS 다운로드 정책 확인 필요",
            "status": "downloaded",
            "download_method": "manual_browser",
            "archive_member_count": result["member_count"],
            "archive_total_rows": result["total_rows"],
            "archive_column_count": len(header),
            "csv_encoding": sorted({member["encoding"] for member in members}),
            "schema_metadata": args.output.as_posix(),
        }
        known = set()
        if args.manifest.exists():
            for line in args.manifest.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    known.add(json.loads(line).get("sha256"))
        if manifest_record["sha256"] not in known:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            with args.manifest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest_record, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("member_count", "total_rows", "header_variant_count", "archive_sha256")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
