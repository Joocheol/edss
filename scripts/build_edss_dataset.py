#!/usr/bin/env python3
"""Build reproducible EDSS topic panels from immutable ZIP archives.

The builder preserves every original value as text, supports direct CSV ZIPs and
nested ZIPs, records archive/member provenance, and keeps person-level employment
data in a separate restricted output area.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


SOURCE_URL = "https://www.edmgr.kr/edss/es/opd/odd/od/es_opd_oddod01_001"
PROVIDER = "교육부·한국교육학술정보원 EDSS"
META_FIELDS = [
    "_source_provider",
    "_source_area",
    "_source_catalog_code",
    "_source_dataset",
    "_source_domn_code",
    "_source_archive",
    "_source_archive_sha256",
    "_source_member",
    "_source_row_number",
    "_source_row_id",
    "_source_row_hash",
    "_panel_year",
]
MISSING_SENTINELS = {"", "-", "--", "NA", "N/A", "n/a", "해당없음", "비공개", "*"}
YEAR_RE = re.compile(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)")
SHORT_YEAR_RE = re.compile(r"\((\d{2})\)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
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


def detect_encoding(sample: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError as error:
            # A fixed-size streaming sample can end halfway through a multibyte
            # character. Accept only that boundary case; reject interior errors.
            if error.start >= len(sample) - 4:
                try:
                    sample[: error.start].decode(encoding)
                    return encoding
                except UnicodeDecodeError:
                    pass
            continue
    raise UnicodeDecodeError("unknown", sample, 0, min(1, len(sample)), "unsupported CSV encoding")


def disambiguate_header(header: list[str]) -> list[str]:
    seen: Counter[str] = Counter()
    result = []
    for raw in header:
        name = unicodedata.normalize("NFC", raw.strip())
        seen[name] += 1
        result.append(name if seen[name] == 1 else f"{name}__duplicate_{seen[name]}")
    return result


def infer_year(text: str) -> str:
    match = YEAR_RE.search(text)
    if match:
        return match.group(1)
    match = SHORT_YEAR_RE.search(text)
    if match:
        value = int(match.group(1))
        return str(2000 + value if value < 50 else 1900 + value)
    return ""


def archive_file_year(path: Path) -> str:
    years = set(YEAR_RE.findall(path.name))
    return next(iter(years)) if len(years) == 1 else "ALL"


def browser_original_filename(entry: dict, file_year: str) -> str:
    if file_year != "ALL":
        return f"{entry['catalog_code']}. {entry['dataset']}.zip"
    advertised = YEAR_RE.findall(entry["advertised_years"])
    suffix = f"({advertised[0][-2:]}-{advertised[-1][-2:]})" if advertised else ""
    return f"{entry['catalog_code']}. {entry['dataset']}{suffix}.zip"


def classify_value(value: str) -> str:
    stripped = value.strip().replace(",", "")
    if not stripped:
        return "empty"
    if re.fullmatch(r"[+-]?\d+", stripped):
        return "integer-like"
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)", stripped):
        return "decimal-like"
    return "string"


def walk_zip(
    archive: zipfile.ZipFile,
    prefix: str,
    callback: Callable[[zipfile.ZipFile, zipfile.ZipInfo, str], None],
) -> None:
    for info in archive.infolist():
        if info.is_dir():
            continue
        member_name = recover_name(info.filename)
        member_path = f"{prefix}!{member_name}" if prefix else member_name
        suffix = Path(member_name).suffix.lower()
        if suffix == ".zip":
            nested_bytes = archive.read(info)
            with zipfile.ZipFile(io.BytesIO(nested_bytes)) as nested:
                bad_member = nested.testzip()
                if bad_member:
                    raise RuntimeError(f"corrupt nested ZIP member: {member_path}!{recover_name(bad_member)}")
                walk_zip(nested, member_path, callback)
        elif suffix == ".csv":
            callback(archive, info, member_path)


def process_archive(path: Path, callback: Callable[[zipfile.ZipFile, zipfile.ZipInfo, str], None]) -> None:
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"corrupt ZIP member: {path}:{recover_name(bad_member)}")
        walk_zip(archive, "", callback)


def open_csv(archive: zipfile.ZipFile, info: zipfile.ZipInfo):
    with archive.open(info) as raw:
        sample = raw.read(65536)
    encoding = detect_encoding(sample)
    raw = archive.open(info)
    text = io.TextIOWrapper(raw, encoding=encoding, newline="")
    return text, encoding


def discover_archives(raw_root: Path, entry: dict) -> list[Path]:
    if "_archive_paths" in entry:
        return sorted(Path(path) for path in entry["_archive_paths"])
    source_root = raw_root / entry["source"]
    if entry["catalog_code"] == "0001":
        directory = source_root / f"0001_{entry['dataset']}_{entry['domn_code']}"
    else:
        directory = source_root / f"{entry['catalog_code']}_{entry['dataset']}"
    return sorted(directory.glob("*.zip"))


def load_rebuild_inventory(path: Path, repo_root: Path) -> list[dict]:
    """Load the canonical full-rebuild inventory as builder entries.

    Archive paths in metadata stay repository-relative.  The private
    ``_archive_paths`` field holds resolved paths only for local execution.
    """

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    entries = []
    seen_domn_codes: set[str] = set()
    seen_archive_paths: set[str] = set()
    required = {"source", "catalog_code", "dataset", "domn_code", "advertised_years", "archive_count", "archive_paths"}
    for row_number, row in enumerate(rows, start=2):
        missing = sorted(field for field in required if not row.get(field))
        if missing:
            raise RuntimeError(f"{path}:{row_number}: missing required values: {missing}")
        domn_code = row["domn_code"]
        if domn_code in seen_domn_codes:
            raise RuntimeError(f"{path}:{row_number}: duplicate domn_code: {domn_code}")
        seen_domn_codes.add(domn_code)
        try:
            archive_paths = json.loads(row["archive_paths"])
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{path}:{row_number}: invalid archive_paths JSON") from error
        if not isinstance(archive_paths, list) or not all(isinstance(value, str) and value for value in archive_paths):
            raise RuntimeError(f"{path}:{row_number}: archive_paths must be a non-empty string list")
        expected_count = int(row["archive_count"])
        if expected_count != len(archive_paths):
            raise RuntimeError(
                f"{path}:{row_number}: archive_count {expected_count} does not match {len(archive_paths)} paths"
            )
        duplicates = sorted(value for value in archive_paths if value in seen_archive_paths)
        if duplicates:
            raise RuntimeError(f"{path}:{row_number}: archive paths repeated across physical units: {duplicates}")
        seen_archive_paths.update(archive_paths)
        resolved = [value if Path(value).is_absolute() else (repo_root / value).as_posix() for value in archive_paths]
        entries.append(
            {
                "source": row["source"],
                "catalog_code": row["catalog_code"],
                "dataset": row["dataset"],
                "domn_code": domn_code,
                "major_area": row.get("major_area", ""),
                "advertised_years": row["advertised_years"],
                "license": "EDSS 다운로드 정책 확인 필요",
                "_archive_paths": resolved,
                "_inventory_archive_paths": archive_paths,
            }
        )
    return entries


def display_path(path: Path, display_root: Path | None) -> str:
    if display_root is not None:
        try:
            return path.resolve().relative_to(display_root.resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def scan_physical_entry(entry: dict, archives: list[Path], display_root: Path | None = None) -> dict:
    members: list[dict] = []
    archive_records: list[dict] = []
    union_fields: list[str] = []
    seen_fields: set[str] = set()
    field_years: dict[str, set[str]] = defaultdict(set)

    for path in archives:
        archive_sha = sha256_file(path)
        file_year = archive_file_year(path)
        archive_display_path = display_path(path, display_root)
        archive_member_start = len(members)

        def inspect_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, member_path: str) -> None:
            text, encoding = open_csv(zf, info)
            try:
                reader = csv.reader(text)
                raw_header = next(reader, [])
                fields = disambiguate_header(raw_header)
                for field in fields:
                    if field not in seen_fields:
                        seen_fields.add(field)
                        union_fields.append(field)
                year_index = fields.index("조사년도") if "조사년도" in fields else None
                inferred = infer_year(member_path) or infer_year(path.name)
                years: set[str] = set()
                row_count = 0
                malformed = 0
                for row in reader:
                    row_count += 1
                    if len(row) != len(fields):
                        malformed += 1
                    year = row[year_index].strip() if year_index is not None and year_index < len(row) else inferred
                    if re.fullmatch(r"(?:19|20)\d{2}", year):
                        years.add(year)
                if not years and inferred:
                    years.add(inferred)
                for field in fields:
                    field_years[field].update(years)
                members.append(
                    {
                        "archive_path": archive_display_path,
                        "archive_sha256": archive_sha,
                        "member_path": member_path,
                        "encoding": encoding,
                        "compressed_bytes": info.compress_size,
                        "uncompressed_bytes": info.file_size,
                        "row_count": row_count,
                        "column_count": len(fields),
                        "original_fields": fields,
                        "observed_years": sorted(years),
                        "malformed_row_count": malformed,
                    }
                )
            finally:
                text.close()

        process_archive(path, inspect_member)
        archive_members = members[archive_member_start:]
        archive_records.append(
            {
                "downloaded_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "provider": PROVIDER,
                "source_url": SOURCE_URL,
                "source": entry["source"],
                "dataset": entry["dataset"],
                "catalog_code": entry["catalog_code"],
                "domn_code": entry["domn_code"],
                "file_year": file_year,
                "advertised_years": entry["advertised_years"],
                "filename": path.name,
                "original_filename": browser_original_filename(entry, file_year),
                "local_path": archive_display_path,
                "size_bytes": path.stat().st_size,
                "sha256": archive_sha,
                "license": entry.get("license", "EDSS 다운로드 정책 확인 필요"),
                "status": "downloaded",
                "download_method": "browser_year" if file_year != "ALL" else "browser_all",
                "archive_member_count": len(archive_members),
                "archive_total_rows": sum(item["row_count"] for item in archive_members),
                "archive_column_count": max((item["column_count"] for item in archive_members), default=0),
                "csv_encoding": sorted({item["encoding"] for item in archive_members}),
            }
        )

    return {
        "generated_at": utc_now(),
        "provider": PROVIDER,
        "source_url": SOURCE_URL,
        "source": entry["source"],
        "dataset": entry["dataset"],
        "catalog_code": entry["catalog_code"],
        "domn_code": entry["domn_code"],
        "advertised_years": entry["advertised_years"],
        "archive_count": len(archives),
        "member_count": len(members),
        "total_rows": sum(item["row_count"] for item in members),
        "header_variant_count": len({tuple(item["original_fields"]) for item in members}),
        "original_fields": union_fields,
        "field_years": {key: sorted(value) for key, value in field_years.items()},
        "members": members,
        "archive_records": archive_records,
    }


def safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", value).strip()


def archive_signature(records: Iterable[dict]) -> str:
    payload = "\n".join(sorted(f"{row['local_path']}:{row['sha256']}" for row in records))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_logical_panel(group: list[tuple[dict, dict]], processed_root: Path, force: bool) -> tuple[dict, list[dict]]:
    first_entry = group[0][0]
    catalog = first_entry["catalog_code"]
    dataset = first_entry["dataset"]
    source = first_entry["source"]
    access_tier = "restricted" if catalog == "0001" else "panel"
    output_dir = processed_root / access_tier / safe_name(source) / f"{catalog}_{safe_name(dataset)}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "panel.csv.gz"
    profile_path = output_dir / "profile.json"

    records = [record for _, profile in group for record in profile["archive_records"]]
    signature = archive_signature(records)
    if not force and output_path.exists() and profile_path.exists():
        previous = json.loads(profile_path.read_text(encoding="utf-8"))
        if previous.get("input_signature") == signature and previous.get("output_sha256") == sha256_file(output_path):
            return previous, previous.get("data_dictionary", [])

    union_fields: list[str] = []
    seen_fields: set[str] = set()
    field_years: dict[str, set[str]] = defaultdict(set)
    for _, profile in group:
        for field in profile["original_fields"]:
            if field not in seen_fields:
                seen_fields.add(field)
                union_fields.append(field)
        for field, years in profile["field_years"].items():
            field_years[field].update(years)

    field_pos = {field: index for index, field in enumerate(union_fields)}
    missing_counts: Counter[str] = Counter()
    nonmissing_counts: Counter[str] = Counter()
    type_samples: dict[str, set[str]] = defaultdict(set)
    type_sample_counts: Counter[str] = Counter()
    sentinel_values: dict[str, set[str]] = defaultdict(set)
    year_counts: Counter[str] = Counter()
    domn_counts: Counter[str] = Counter()
    duplicate_hashes = 0
    seen_row_hashes: set[int] = set()
    malformed_rows = 0
    total_rows = 0
    identifier_fields = [
        field
        for field in union_fields
        if field in {"조사년도", "적용년도", "개방ID", "학교ID", "학교명", "본분교명", "캠퍼스명", "학교구분명", "학교유형명", "설립유형명", "시도명", "지역명"}
        or any(token in field for token in ("학과명", "전공명", "학과코드", "전공코드", "캠퍼스코드"))
    ]

    temp_path = output_path.with_suffix(output_path.suffix + ".part")
    with gzip.open(temp_path, "wt", encoding="utf-8", newline="", compresslevel=6) as output:
        writer = csv.writer(output)
        writer.writerow(META_FIELDS + union_fields)
        for entry, profile in group:
            record_by_path = {record["local_path"]: record for record in profile["archive_records"]}
            for archive_path_text, archive_record in record_by_path.items():
                archive_path = Path(archive_path_text)
                archive_sha = archive_record["sha256"]

                def write_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, member_path: str) -> None:
                    nonlocal total_rows, malformed_rows, duplicate_hashes
                    text, _ = open_csv(zf, info)
                    try:
                        reader = csv.reader(text)
                        fields = disambiguate_header(next(reader, []))
                        destinations = [field_pos[field] for field in fields]
                        year_index = fields.index("조사년도") if "조사년도" in fields else None
                        fallback_year = infer_year(member_path) or infer_year(archive_path.name)
                        for row_number, row in enumerate(reader, start=2):
                            total_rows += 1
                            if len(row) != len(fields):
                                malformed_rows += 1
                            row = row[: len(fields)] + [""] * max(0, len(fields) - len(row))
                            values = [""] * len(union_fields)
                            for index, value in enumerate(row):
                                field = fields[index]
                                value = unicodedata.normalize("NFC", value)
                                values[destinations[index]] = value
                                if not value.strip():
                                    missing_counts[field] += 1
                                else:
                                    nonmissing_counts[field] += 1
                                if value.strip() in MISSING_SENTINELS:
                                    sentinel_values[field].add(value.strip())
                                if type_sample_counts[field] < 10000:
                                    type_samples[field].add(classify_value(value))
                                    type_sample_counts[field] += 1
                            year = row[year_index].strip() if year_index is not None else fallback_year
                            if not re.fullmatch(r"(?:19|20)\d{2}", year):
                                year = fallback_year
                            year_counts[year or "unknown"] += 1
                            domn_counts[entry["domn_code"]] += 1
                            original_payload = "\x1f".join(row).encode("utf-8")
                            row_hash = hashlib.sha256(original_payload).hexdigest()
                            compact_hash = int.from_bytes(hashlib.blake2b(original_payload, digest_size=8).digest(), "big")
                            if compact_hash in seen_row_hashes:
                                duplicate_hashes += 1
                            else:
                                seen_row_hashes.add(compact_hash)
                            identity = f"{entry['domn_code']}|{archive_sha}|{member_path}|{row_number}"
                            row_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                            metadata = [
                                PROVIDER,
                                source,
                                catalog,
                                dataset,
                                entry["domn_code"],
                                archive_path.as_posix(),
                                archive_sha,
                                member_path,
                                str(row_number),
                                row_id,
                                row_hash,
                                year,
                            ]
                            writer.writerow(metadata + values)
                    finally:
                        text.close()

                process_archive(archive_path, write_member)
    os.replace(temp_path, output_path)

    output_sha = sha256_file(output_path)
    data_dictionary = []
    for position, field in enumerate(union_fields, start=1):
        observed_types = sorted(type_samples.get(field, set()) - {"empty"})
        data_dictionary.append(
            {
                "source": source,
                "catalog_code": catalog,
                "dataset": dataset,
                "field_position": position,
                "original_field_name": field,
                "korean_meaning": field,
                "storage_type": "string",
                "observed_value_type": ",".join(observed_types) or "empty-only",
                "unit": "공식 정의 미확인",
                "missing_value_definition": "원본 공란·기호의 의미는 공식 설명서 확인 필요",
                "observed_missing_tokens": json.dumps(sorted(sentinel_values.get(field, set())), ensure_ascii=False),
                "first_year": min(field_years.get(field, {""})),
                "last_year": max(field_years.get(field, {""})),
                "identifier_role": "candidate" if field in identifier_fields else "",
            }
        )

    profile = {
        "built_at": utc_now(),
        "provider": PROVIDER,
        "source": source,
        "catalog_code": catalog,
        "dataset": dataset,
        "access_tier": access_tier,
        "grain_status": "원본 행 단위; 분석용 후보키는 별도 검증 필요",
        "output_path": output_path.as_posix(),
        "output_bytes": output_path.stat().st_size,
        "output_sha256": output_sha,
        "input_signature": signature,
        "input_archive_count": len(records),
        "physical_domn_codes": sorted(domn_counts),
        "row_count": total_rows,
        "original_column_count": len(union_fields),
        "metadata_column_count": len(META_FIELDS),
        "year_counts": dict(sorted(year_counts.items())),
        "malformed_row_count": malformed_rows,
        "exact_original_row_duplicate_count": duplicate_hashes,
        "exact_original_row_duplicate_rate": duplicate_hashes / total_rows if total_rows else 0,
        "candidate_identifier_fields": identifier_fields,
        "candidate_identifier_missing_counts": {field: total_rows - nonmissing_counts[field] for field in identifier_fields},
        "data_dictionary": data_dictionary,
    }
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return profile, data_dictionary


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_manifest(path: Path, records: list[dict]) -> None:
    existing: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                existing[record["sha256"]] = record
    for record in records:
        existing[record["sha256"]] = record
    ordered = sorted(existing.values(), key=lambda row: (row.get("source", ""), row.get("catalog_code", ""), row.get("domn_code", ""), row.get("file_year", ""), row.get("local_path", "")))
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8") as handle:
        for record in ordered:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/edss_priority_datasets.json"))
    parser.add_argument("--inventory", type=Path, help="Canonical full-rebuild physical-unit inventory CSV")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/edss"))
    parser.add_argument("--processed-root", type=Path, default=Path("data/processed/edss"))
    parser.add_argument("--metadata-root", type=Path, default=Path("data/metadata"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()

    entries = (
        load_rebuild_inventory(args.inventory, args.repo_root)
        if args.inventory
        else json.loads(args.config.read_text(encoding="utf-8"))
    )
    physical: list[tuple[dict, dict]] = []
    manifest_records: list[dict] = []
    missing = []
    for entry in entries:
        archives = discover_archives(args.raw_root, entry)
        if not archives:
            missing.append({"domn_code": entry["domn_code"], "dataset": entry["dataset"]})
            continue
        profile = scan_physical_entry(entry, archives, display_root=args.repo_root if args.inventory else None)
        schema_name = (
            f"edss_{entry['catalog_code']}_{entry['domn_code']}_schema.json"
            if args.inventory or entry["catalog_code"] == "0001"
            else f"edss_{entry['catalog_code']}_schema.json"
        )
        schema_path = args.metadata_root / schema_name
        for record in profile["archive_records"]:
            record["schema_metadata"] = schema_path.as_posix()
        write_json(schema_path, {key: value for key, value in profile.items() if key != "archive_records"})
        physical.append((entry, profile))
        manifest_records.extend(profile["archive_records"])

    if missing:
        raise RuntimeError(f"missing raw archives: {missing}")
    write_manifest(args.metadata_root / "edss_file_manifest.jsonl", manifest_records)

    if args.inspect_only:
        print(json.dumps({"physical_dataset_count": len(physical), "archive_count": len(manifest_records)}, ensure_ascii=False))
        return 0

    logical_groups: dict[tuple[str, str, str], list[tuple[dict, dict]]] = defaultdict(list)
    for entry, profile in physical:
        logical_groups[(entry["source"], entry["catalog_code"], entry["dataset"])].append((entry, profile))

    catalog_rows = []
    dictionary_rows: list[dict] = []
    quality_profiles = []
    for key in sorted(logical_groups):
        profile, dictionary = build_logical_panel(logical_groups[key], args.processed_root, args.force)
        dictionary_rows.extend(dictionary)
        quality_profiles.append({key: value for key, value in profile.items() if key != "data_dictionary"})
        catalog_rows.append(
            {
                "source": profile["source"],
                "catalog_code": profile["catalog_code"],
                "dataset": profile["dataset"],
                "access_tier": profile["access_tier"],
                "physical_domn_codes": ",".join(profile["physical_domn_codes"]),
                "row_count": profile["row_count"],
                "column_count": profile["original_column_count"],
                "first_year": min(profile["year_counts"], default=""),
                "last_year": max(profile["year_counts"], default=""),
                "input_archive_count": profile["input_archive_count"],
                "output_path": profile["output_path"],
                "output_bytes": profile["output_bytes"],
                "output_sha256": profile["output_sha256"],
            }
        )

    catalog_path = args.metadata_root / "edss_panel_catalog.csv"
    with catalog_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(catalog_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(catalog_rows)
    dictionary_path = args.metadata_root / "edss_panel_data_dictionary.csv"
    with dictionary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dictionary_rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(dictionary_rows)
    write_json(
        args.metadata_root / "edss_panel_quality_report.json",
        {
            "generated_at": utc_now(),
            "logical_dataset_count": len(catalog_rows),
            "physical_dataset_count": len(physical),
            "raw_archive_count": len(manifest_records),
            "profiles": quality_profiles,
        },
    )
    print(json.dumps({"logical_dataset_count": len(catalog_rows), "physical_dataset_count": len(physical), "raw_archive_count": len(manifest_records), "total_rows": sum(row["row_count"] for row in catalog_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
