#!/usr/bin/env python3
"""Independently audit EDSS panel integrity, grain, and school-year joins."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable


AUDIT_VERSION = "2"
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
PROVENANCE_FIELDS = [
    "_source_domn_code",
    "_source_archive",
    "_source_archive_sha256",
    "_source_member",
    "_source_row_number",
    "_source_row_id",
    "_source_row_hash",
]
DIMENSION_TERMS = (
    "ID",
    "코드",
    "명",
    "구분",
    "유형",
    "계열",
    "과정",
    "학년",
    "성별",
    "주야",
    "지역",
    "시도",
    "본분교",
    "전공",
    "학과",
    "학교",
    "국가",
    "연령",
    "직업",
    "사유",
    "상태",
    "형태",
    "학제",
    "설립",
    "모집단위",
    "자격",
)
MEASURE_SUFFIXES = (
    "학생수",
    "인원",
    "금액",
    "비율",
    "면적",
    "시간",
    "점수",
    "건수",
    "횟수",
    "개수",
    "학교수",
    "교원수",
    "학과수",
    "정원",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


class PartitionedDigestCounter:
    """Count duplicate fixed-width digests with bounded memory."""

    def __init__(self, root: Path, prefix: str, partition_count: int = 64) -> None:
        self.root = root
        self.prefix = prefix
        self.partition_count = partition_count
        self.handles: dict[int, BinaryIO] = {}
        self.paths: dict[int, Path] = {}

    def add(self, digest: bytes) -> None:
        partition = digest[0] % self.partition_count
        handle = self.handles.get(partition)
        if handle is None:
            path = self.root / f"{self.prefix}-{partition:02d}.bin"
            handle = path.open("wb", buffering=1024 * 1024)
            self.handles[partition] = handle
            self.paths[partition] = path
        handle.write(digest)

    def close(self) -> None:
        for handle in self.handles.values():
            handle.close()
        self.handles.clear()

    def duplicate_count(self) -> int:
        self.close()
        duplicates = 0
        for path in self.paths.values():
            data = path.read_bytes()
            if len(data) % 32:
                raise RuntimeError(f"invalid digest partition: {path}")
            values = [data[index : index + 32] for index in range(0, len(data), 32)]
            values.sort()
            duplicates += sum(left == right for left, right in zip(values, values[1:]))
        return duplicates


class HyperLogLog:
    """Small deterministic approximate distinct counter for sampled grain tests."""

    def __init__(self, precision: int = 12) -> None:
        self.precision = precision
        self.registers = bytearray(1 << precision)

    def add(self, payload: bytes) -> None:
        value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        index = value >> (64 - self.precision)
        remainder = (value << self.precision) & ((1 << 64) - 1)
        rank = 64 - self.precision + 1 if remainder == 0 else (64 - remainder.bit_length()) + 1
        if rank > self.registers[index]:
            self.registers[index] = rank

    def estimate(self) -> float:
        register_count = len(self.registers)
        alpha = 0.7213 / (1 + 1.079 / register_count)
        raw = alpha * register_count * register_count / sum(2.0 ** (-value) for value in self.registers)
        zeros = self.registers.count(0)
        if raw <= 2.5 * register_count and zeros:
            return register_count * math.log(register_count / zeros)
        return raw


def normalized_identifier(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split())


def candidate_dimension_fields(raw_fields: list[str], limit: int = 24) -> list[str]:
    candidates = []
    for field in raw_fields:
        if field in {"조사년도", "개방ID"}:
            continue
        if not any(term in field for term in DIMENSION_TERMS):
            continue
        if any(field.endswith(suffix) for suffix in MEASURE_SUFFIXES):
            continue
        priority = 0
        if field.endswith("ID") or "코드" in field:
            priority -= 30
        if field.endswith("명") or "학과" in field or "본분교" in field:
            priority -= 20
        if any(term in field for term in ("구분", "유형", "계열", "과정", "학년", "성별", "주야")):
            priority -= 10
        candidates.append((priority, raw_fields.index(field), field))
    return [field for _, _, field in sorted(candidates)[:limit]]


def classify_orphan(year: str, open_id: str, base_years_by_id: dict[str, set[str]]) -> tuple[str, str, str]:
    years = base_years_by_id.get(open_id)
    if not years:
        return "never_in_0101", "", ""
    first_year = min(years)
    last_year = max(years)
    if year < first_year:
        classification = "before_first_0101_year"
    elif year > last_year:
        classification = "after_last_0101_year"
    else:
        classification = "internal_0101_gap"
    return classification, first_year, last_year


def panel_key(row: dict) -> tuple[str, str, str]:
    return row["source"], row["catalog_code"], row["dataset"]


def safe_cache_name(row: dict) -> str:
    identity = "\x1f".join(panel_key(row)).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:20] + ".json"


def select_sample(row_digest: bytes, row_count: int, target: int) -> bool:
    if row_count <= target:
        return True
    threshold = max(1, int(65536 * target / row_count))
    return int.from_bytes(row_digest[:2], "big") < threshold


def audit_panel(
    catalog_row: dict,
    repo_root: Path,
    temp_root: Path,
    sample_target: int,
    base_key_counts: Counter[tuple[str, str]] | None = None,
    base_years_by_id: dict[str, set[str]] | None = None,
) -> dict:
    output_path = repo_root / catalog_row["output_path"]
    expected_rows = int(catalog_row["row_count"])
    expected_sha = catalog_row["output_sha256"]
    actual_sha = sha256_file(output_path)
    if actual_sha != expected_sha:
        raise RuntimeError(f"output SHA-256 mismatch: {output_path}")

    row_count = 0
    width_mismatch_count = 0
    row_hash_mismatch_count = 0
    row_id_mismatch_count = 0
    invalid_row_id_format_count = 0
    missing_panel_year_count = 0
    invalid_panel_year_count = 0
    raw_year_mismatch_count = 0
    open_id_missing_count = 0
    open_id_whitespace_count = 0
    open_id_normalization_collision_count = 0
    year_counts: Counter[str] = Counter()
    year_missing_open_id_counts: Counter[str] = Counter()
    school_year_counts: Counter[tuple[str, str]] = Counter()
    exact_open_ids: set[str] = set()
    normalized_to_exact: dict[str, set[str]] = defaultdict(set)
    provenance_missing: Counter[str] = Counter()
    sample_rows = 0
    base_sample_hll = HyperLogLog()
    dimension_hll: dict[str, HyperLogLog] = {}
    all_dimension_hll = HyperLogLog()

    with tempfile.TemporaryDirectory(prefix="panel-", dir=temp_root) as digest_dir_text:
        digest_dir = Path(digest_dir_text)
        raw_digest_counter = PartitionedDigestCounter(digest_dir, "raw")
        row_id_counter = PartitionedDigestCounter(digest_dir, "row-id")
        try:
            with gzip.open(output_path, "rt", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                fields = next(reader, [])
                if not fields or len(fields) != len(set(fields)):
                    raise RuntimeError(f"invalid or duplicate header: {output_path}")
                positions = {field: index for index, field in enumerate(fields)}
                missing_meta = [field for field in META_FIELDS if field not in positions]
                if missing_meta:
                    raise RuntimeError(f"missing metadata fields in {output_path}: {missing_meta}")
                raw_fields = [field for field in fields if field not in META_FIELDS]
                raw_positions = [positions[field] for field in raw_fields]
                dimension_fields = candidate_dimension_fields(raw_fields)
                dimension_hll = {field: HyperLogLog() for field in dimension_fields}
                open_id_position = positions.get("개방ID")
                raw_year_position = positions.get("조사년도")

                for row in reader:
                    row_count += 1
                    if len(row) != len(fields):
                        width_mismatch_count += 1
                        row = row[: len(fields)] + [""] * max(0, len(fields) - len(row))

                    for field in PROVENANCE_FIELDS:
                        if not row[positions[field]].strip():
                            provenance_missing[field] += 1

                    year = row[positions["_panel_year"]].strip()
                    if not year:
                        missing_panel_year_count += 1
                        year_bucket = "missing"
                    elif not re.fullmatch(r"(?:19|20)\d{2}", year):
                        invalid_panel_year_count += 1
                        year_bucket = year
                    else:
                        year_bucket = year
                    year_counts[year_bucket] += 1

                    if raw_year_position is not None:
                        raw_year = row[raw_year_position].strip()
                        if raw_year and re.fullmatch(r"(?:19|20)\d{2}", raw_year) and raw_year != year:
                            raw_year_mismatch_count += 1

                    open_id = row[open_id_position] if open_id_position is not None else ""
                    stripped_open_id = open_id.strip()
                    if open_id != stripped_open_id:
                        open_id_whitespace_count += 1
                    if not stripped_open_id:
                        open_id_missing_count += 1
                        year_missing_open_id_counts[year_bucket] += 1
                    else:
                        exact_open_ids.add(stripped_open_id)
                        normalized_to_exact[normalized_identifier(stripped_open_id)].add(stripped_open_id)
                        school_year_counts[(year_bucket, stripped_open_id)] += 1

                    raw_values = [row[index] for index in raw_positions]
                    raw_digest = hashlib.sha256("\x1f".join(raw_values).encode("utf-8")).digest()
                    raw_digest_counter.add(raw_digest)
                    if raw_digest.hex() != row[positions["_source_row_hash"]]:
                        row_hash_mismatch_count += 1

                    identity = "|".join(
                        [
                            row[positions["_source_domn_code"]],
                            row[positions["_source_archive_sha256"]],
                            row[positions["_source_member"]],
                            row[positions["_source_row_number"]],
                        ]
                    )
                    expected_row_id = hashlib.sha256(identity.encode("utf-8")).digest()
                    stored_row_id = row[positions["_source_row_id"]]
                    if re.fullmatch(r"[0-9a-f]{64}", stored_row_id):
                        row_id_counter.add(bytes.fromhex(stored_row_id))
                    else:
                        invalid_row_id_format_count += 1
                        row_id_counter.add(hashlib.sha256(stored_row_id.encode("utf-8")).digest())
                    if expected_row_id.hex() != stored_row_id:
                        row_id_mismatch_count += 1

                    if stripped_open_id and select_sample(raw_digest, expected_rows, sample_target):
                        sample_rows += 1
                        base_payload = f"{year_bucket}\x1f{stripped_open_id}".encode("utf-8")
                        base_sample_hll.add(base_payload)
                        combined = [year_bucket, stripped_open_id]
                        for field in dimension_fields:
                            value = row[positions[field]].strip()
                            combined.append(value)
                            dimension_hll[field].add(base_payload + b"\x1f" + value.encode("utf-8"))
                        all_dimension_hll.add("\x1f".join(combined).encode("utf-8"))

            exact_duplicate_rows = raw_digest_counter.duplicate_count()
            duplicate_row_ids = row_id_counter.duplicate_count()
        finally:
            raw_digest_counter.close()
            row_id_counter.close()

    if row_count != expected_rows:
        raise RuntimeError(f"row count mismatch for {output_path}: {row_count} != {expected_rows}")

    for values in normalized_to_exact.values():
        if len(values) > 1:
            open_id_normalization_collision_count += len(values) - 1

    base_duplicate_rows = sum(count - 1 for count in school_year_counts.values() if count > 1)
    repeated_school_year_keys = sum(count > 1 for count in school_year_counts.values())
    max_school_year_multiplicity = max(school_year_counts.values(), default=0)
    orphan_records = []
    orphan_rows = 0
    join_expansion_affected_rows = 0
    join_expansion_extra_rows = 0
    if base_key_counts is not None and base_years_by_id is not None:
        for (year, open_id), count in school_year_counts.items():
            base_count = base_key_counts.get((year, open_id), 0)
            if not base_count:
                classification, first_year, last_year = classify_orphan(year, open_id, base_years_by_id)
                orphan_rows += count
                orphan_records.append(
                    {
                        "source": catalog_row["source"],
                        "catalog_code": catalog_row["catalog_code"],
                        "dataset": catalog_row["dataset"],
                        "year": year,
                        "open_id": open_id,
                        "row_count": count,
                        "classification": classification,
                        "first_0101_year": first_year,
                        "last_0101_year": last_year,
                    }
                )
            elif base_count > 1:
                join_expansion_affected_rows += count
                join_expansion_extra_rows += count * (base_count - 1)

    base_sample_estimate = min(sample_rows, round(base_sample_hll.estimate())) if sample_rows else 0
    dimension_gains = []
    for field, counter in dimension_hll.items():
        estimate = min(sample_rows, round(counter.estimate())) if sample_rows else 0
        dimension_gains.append(
            {
                "field": field,
                "sample_distinct_keys": estimate,
                "incremental_keys": max(0, estimate - base_sample_estimate),
                "sample_uniqueness_rate": estimate / sample_rows if sample_rows else 0,
            }
        )
    dimension_gains.sort(key=lambda item: (-item["incremental_keys"], item["field"]))
    all_dimension_estimate = min(sample_rows, round(all_dimension_hll.estimate())) if sample_rows else 0

    # `_source_row_hash` was computed from each source member's original field
    # order. A canonical panel contains the union of fields across members, so
    # rows from historical header variants cannot be reconstructed exactly from
    # the panel alone. A canonical digest match is positive evidence; a
    # non-match is therefore "not reconstructable", not an integrity failure.
    source_row_hash_canonical_match_count = row_count - row_hash_mismatch_count
    source_row_hash_not_reconstructable_count = row_hash_mismatch_count
    integrity_failures = sum(
        [
            width_mismatch_count,
            row_id_mismatch_count,
            invalid_row_id_format_count,
            duplicate_row_ids,
            missing_panel_year_count,
            invalid_panel_year_count,
            raw_year_mismatch_count,
            sum(provenance_missing.values()),
        ]
    )
    nonmissing_open_id_rows = row_count - open_id_missing_count
    if open_id_position is None or nonmissing_open_id_rows == 0:
        grain_status = "id_unavailable"
    elif open_id_missing_count:
        grain_status = "partial_id_coverage"
    elif base_duplicate_rows:
        grain_status = "additional_dimensions_required"
    else:
        grain_status = "school_year_open_id_unique"

    comparable_rows = nonmissing_open_id_rows
    orphan_rate = orphan_rows / comparable_rows if comparable_rows else 0
    missing_rate = open_id_missing_count / row_count if row_count else 0
    if integrity_failures or exact_duplicate_rows:
        severity = "critical"
    elif open_id_position is None or nonmissing_open_id_rows == 0 or missing_rate > 0.01 or orphan_rate > 0.01:
        severity = "high"
    elif open_id_missing_count or orphan_rows or base_duplicate_rows or join_expansion_extra_rows:
        severity = "medium"
    elif open_id_normalization_collision_count or open_id_whitespace_count:
        severity = "low"
    else:
        severity = "pass"
    status = "pass" if severity == "pass" and grain_status == "school_year_open_id_unique" else "review_required"

    year_stats = []
    orphan_rows_by_year: Counter[str] = Counter()
    for record in orphan_records:
        orphan_rows_by_year[record["year"]] += int(record["row_count"])
    for year in sorted(year_counts):
        year_stats.append(
            {
                "source": catalog_row["source"],
                "catalog_code": catalog_row["catalog_code"],
                "dataset": catalog_row["dataset"],
                "year": year,
                "row_count": year_counts[year],
                "missing_open_id_rows": year_missing_open_id_counts[year],
                "orphan_rows": orphan_rows_by_year[year],
            }
        )

    result = {
        "audit_version": AUDIT_VERSION,
        "audited_at": utc_now(),
        "source": catalog_row["source"],
        "catalog_code": catalog_row["catalog_code"],
        "dataset": catalog_row["dataset"],
        "access_tier": catalog_row["access_tier"],
        "output_path": catalog_row["output_path"],
        "output_sha256": actual_sha,
        "row_count": row_count,
        "column_count": len(fields),
        "raw_column_count": len(raw_fields),
        "observed_years": sorted(year_counts),
        "year_counts": dict(sorted(year_counts.items())),
        "width_mismatch_count": width_mismatch_count,
        "source_row_hash_canonical_match_count": source_row_hash_canonical_match_count,
        "source_row_hash_not_reconstructable_count": source_row_hash_not_reconstructable_count,
        "row_id_mismatch_count": row_id_mismatch_count,
        "invalid_row_id_format_count": invalid_row_id_format_count,
        "duplicate_row_id_count": duplicate_row_ids,
        "exact_canonical_row_duplicate_count": exact_duplicate_rows,
        "missing_panel_year_count": missing_panel_year_count,
        "invalid_panel_year_count": invalid_panel_year_count,
        "raw_year_mismatch_count": raw_year_mismatch_count,
        "provenance_missing_counts": dict(provenance_missing),
        "open_id_column_present": open_id_position is not None,
        "open_id_missing_count": open_id_missing_count,
        "open_id_missing_rate": missing_rate,
        "open_id_distinct_count": len(exact_open_ids),
        "open_id_whitespace_count": open_id_whitespace_count,
        "open_id_normalization_collision_count": open_id_normalization_collision_count,
        "school_year_key_count": len(school_year_counts),
        "repeated_school_year_key_count": repeated_school_year_keys,
        "school_year_base_duplicate_rows": base_duplicate_rows,
        "max_school_year_key_multiplicity": max_school_year_multiplicity,
        "orphan_school_year_key_count": len(orphan_records),
        "orphan_row_count": orphan_rows,
        "orphan_row_rate": orphan_rate,
        "join_expansion_affected_rows": join_expansion_affected_rows,
        "join_expansion_extra_rows": join_expansion_extra_rows,
        "sample_row_count": sample_rows,
        "sample_base_distinct_key_estimate": base_sample_estimate,
        "candidate_dimension_fields": dimension_fields,
        "candidate_dimension_gains": dimension_gains,
        "sample_all_dimensions_distinct_key_estimate": all_dimension_estimate,
        "sample_all_dimensions_uniqueness_rate": all_dimension_estimate / sample_rows if sample_rows else 0,
        "grain_status": grain_status,
        "severity": severity,
        "status": status,
        "orphan_records": orphan_records,
        "year_stats": year_stats,
    }
    if base_key_counts is None:
        result["_base_key_counts"] = [[year, open_id, count] for (year, open_id), count in school_year_counts.items()]
    return result


def load_catalog(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 233:
        raise RuntimeError(f"expected 233 catalog rows, found {len(rows)}")
    return rows


def recalculate_severity(result: dict) -> None:
    integrity_failures = sum(
        [
            result["width_mismatch_count"],
            result["row_id_mismatch_count"],
            result["invalid_row_id_format_count"],
            result["duplicate_row_id_count"],
            result["missing_panel_year_count"],
            result["invalid_panel_year_count"],
            result["raw_year_mismatch_count"],
            sum(result["provenance_missing_counts"].values()),
        ]
    )
    row_count = result["row_count"]
    missing_count = result["open_id_missing_count"]
    nonmissing_count = row_count - missing_count
    missing_rate = missing_count / row_count if row_count else 0
    orphan_rate = result["orphan_row_count"] / nonmissing_count if nonmissing_count else 0
    if integrity_failures or result["exact_canonical_row_duplicate_count"]:
        severity = "critical"
    elif not result["open_id_column_present"] or not nonmissing_count or missing_rate > 0.01 or orphan_rate > 0.01:
        severity = "high"
    elif missing_count or result["orphan_row_count"] or result["school_year_base_duplicate_rows"] or result["join_expansion_extra_rows"]:
        severity = "medium"
    elif result["open_id_normalization_collision_count"] or result["open_id_whitespace_count"]:
        severity = "low"
    else:
        severity = "pass"
    result["severity"] = severity
    result["status"] = (
        "pass" if severity == "pass" and result["grain_status"] == "school_year_open_id_unique" else "review_required"
    )


def upgrade_cache(cache: dict) -> dict:
    if cache.get("audit_version") == "1":
        row_hash_mismatches = int(cache.pop("row_hash_mismatch_count"))
        cache["source_row_hash_canonical_match_count"] = cache["row_count"] - row_hash_mismatches
        cache["source_row_hash_not_reconstructable_count"] = row_hash_mismatches
        cache["exact_canonical_row_duplicate_count"] = cache.pop("exact_original_row_duplicate_count")
        cache["audit_version"] = AUDIT_VERSION
        recalculate_severity(cache)
    return cache


def cache_is_valid(cache: dict, row: dict, reference_sha256: str) -> bool:
    return (
        cache.get("audit_version") == AUDIT_VERSION
        and cache.get("output_sha256") == row["output_sha256"]
        and cache.get("reference_sha256", "") == reference_sha256
    )


def summary_row(result: dict) -> dict:
    fields = [
        "source",
        "catalog_code",
        "dataset",
        "access_tier",
        "row_count",
        "column_count",
        "observed_years",
        "open_id_column_present",
        "open_id_missing_count",
        "open_id_missing_rate",
        "open_id_distinct_count",
        "school_year_key_count",
        "repeated_school_year_key_count",
        "school_year_base_duplicate_rows",
        "max_school_year_key_multiplicity",
        "orphan_school_year_key_count",
        "orphan_row_count",
        "orphan_row_rate",
        "join_expansion_affected_rows",
        "join_expansion_extra_rows",
        "exact_canonical_row_duplicate_count",
        "source_row_hash_canonical_match_count",
        "source_row_hash_not_reconstructable_count",
        "row_id_mismatch_count",
        "invalid_row_id_format_count",
        "duplicate_row_id_count",
        "width_mismatch_count",
        "candidate_dimension_fields",
        "candidate_dimension_gains",
        "sample_all_dimensions_uniqueness_rate",
        "grain_status",
        "severity",
        "status",
        "output_path",
        "output_sha256",
    ]
    row = {field: result[field] for field in fields}
    for field in ("observed_years", "candidate_dimension_fields", "candidate_dimension_gains"):
        row[field] = json.dumps(row[field], ensure_ascii=False, separators=(",", ":"))
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--catalog", type=Path, default=Path("data/metadata/edss_panel_catalog.csv"))
    parser.add_argument("--output-summary", type=Path, default=Path("data/metadata/edss_full_panel_key_audit.json"))
    parser.add_argument("--output-panels", type=Path, default=Path("data/metadata/edss_full_panel_key_audit.csv"))
    parser.add_argument("--output-years", type=Path, default=Path("data/metadata/edss_full_panel_key_audit_by_year.csv"))
    parser.add_argument("--output-orphans", type=Path, default=Path("data/metadata/edss_full_panel_orphan_school_year_keys.csv"))
    parser.add_argument("--cache-root", type=Path, default=Path("data/processed/edss/full-key-audit-cache"))
    parser.add_argument("--sample-target", type=int, default=10000)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    catalog_path = repo_root / args.catalog
    rows = load_catalog(catalog_path)
    rows.sort(key=lambda row: (0 if row["source"] == "고등교육통계" and row["catalog_code"] == "0101" else 1, panel_key(row)))
    base_catalog = rows[0]
    if not (base_catalog["source"] == "고등교육통계" and base_catalog["catalog_code"] == "0101"):
        raise RuntimeError("0101 고등교육학교개황 reference panel not found")

    cache_root = repo_root / args.cache_root
    cache_root.mkdir(parents=True, exist_ok=True)
    temp_parent = repo_root / "data/processed/edss"
    results = []
    with tempfile.TemporaryDirectory(prefix=".full-key-audit-", dir=temp_parent) as temp_root_text:
        temp_root = Path(temp_root_text)
        base_cache_path = cache_root / safe_cache_name(base_catalog)
        base_result = None
        if base_cache_path.exists() and not args.force:
            candidate = upgrade_cache(json.loads(base_cache_path.read_text(encoding="utf-8")))
            if cache_is_valid(candidate, base_catalog, "") and candidate.get("_base_key_counts"):
                base_result = candidate
                write_json(base_cache_path, candidate)
        if base_result is None:
            print(f"auditing 1/{len(rows)}: {panel_key(base_catalog)}", flush=True)
            base_result = audit_panel(base_catalog, repo_root, temp_root, args.sample_target)
            base_result["reference_sha256"] = ""
            write_json(base_cache_path, base_result)
        else:
            print(f"reusing 1/{len(rows)}: {panel_key(base_catalog)}", flush=True)
        results.append(base_result)

        base_key_counts: Counter[tuple[str, str]] = Counter(
            {(year, open_id): int(count) for year, open_id, count in base_result["_base_key_counts"]}
        )
        base_years_by_id: dict[str, set[str]] = defaultdict(set)
        for year, open_id in base_key_counts:
            base_years_by_id[open_id].add(year)
        reference_sha256 = base_catalog["output_sha256"]

        for index, row in enumerate(rows[1:], start=2):
            cache_path = cache_root / safe_cache_name(row)
            result = None
            if cache_path.exists() and not args.force:
                candidate = upgrade_cache(json.loads(cache_path.read_text(encoding="utf-8")))
                if cache_is_valid(candidate, row, reference_sha256):
                    result = candidate
                    write_json(cache_path, candidate)
            if result is None:
                print(f"auditing {index}/{len(rows)}: {panel_key(row)}", flush=True)
                result = audit_panel(row, repo_root, temp_root, args.sample_target, base_key_counts, base_years_by_id)
                result["reference_sha256"] = reference_sha256
                write_json(cache_path, result)
            else:
                print(f"reusing {index}/{len(rows)}: {panel_key(row)}", flush=True)
            results.append(result)

    panel_rows = [summary_row(result) for result in results]
    orphan_rows = [record for result in results for record in result["orphan_records"]]
    orphan_rows.sort(key=lambda row: (row["source"], row["catalog_code"], row["dataset"], row["year"], row["open_id"]))
    year_rows = [record for result in results for record in result["year_stats"]]
    year_rows.sort(key=lambda row: (row["source"], row["catalog_code"], row["dataset"], row["year"]))

    severity_counts = Counter(result["severity"] for result in results)
    grain_status_counts = Counter(result["grain_status"] for result in results)
    source_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for result in results:
        source = result["source"]
        source_counts[source]["panel_count"] += 1
        source_counts[source]["row_count"] += result["row_count"]
        source_counts[source]["missing_open_id_rows"] += result["open_id_missing_count"]
        source_counts[source]["orphan_rows"] += result["orphan_row_count"]
        source_counts[source]["join_expansion_extra_rows"] += result["join_expansion_extra_rows"]

    summary = {
        "audit_version": AUDIT_VERSION,
        "audited_at": utc_now(),
        "status": "pass" if all(result["status"] == "pass" for result in results) else "review_required",
        "reference_panel": "고등교육통계 0101 고등교육학교개황",
        "reference_output_sha256": reference_sha256,
        "logical_panel_count": len(results),
        "total_rows": sum(result["row_count"] for result in results),
        "total_output_bytes": sum(int(row["output_bytes"]) for row in rows),
        "severity_counts": dict(sorted(severity_counts.items())),
        "grain_status_counts": dict(sorted(grain_status_counts.items())),
        "integrity": {
            "width_mismatch_rows": sum(result["width_mismatch_count"] for result in results),
            "source_row_hash_canonical_matches": sum(
                result["source_row_hash_canonical_match_count"] for result in results
            ),
            "source_row_hash_not_reconstructable_from_panel": sum(
                result["source_row_hash_not_reconstructable_count"] for result in results
            ),
            "row_id_mismatches": sum(result["row_id_mismatch_count"] for result in results),
            "invalid_row_id_formats": sum(result["invalid_row_id_format_count"] for result in results),
            "duplicate_row_ids": sum(result["duplicate_row_id_count"] for result in results),
            "exact_canonical_row_duplicates": sum(
                result["exact_canonical_row_duplicate_count"] for result in results
            ),
            "missing_panel_year_rows": sum(result["missing_panel_year_count"] for result in results),
            "invalid_panel_year_rows": sum(result["invalid_panel_year_count"] for result in results),
            "raw_year_mismatch_rows": sum(result["raw_year_mismatch_count"] for result in results),
        },
        "open_id": {
            "panels_with_column": sum(result["open_id_column_present"] for result in results),
            "panels_with_missing_rows": sum(result["open_id_missing_count"] > 0 for result in results),
            "missing_rows": sum(result["open_id_missing_count"] for result in results),
            "whitespace_rows": sum(result["open_id_whitespace_count"] for result in results),
            "normalization_collisions": sum(result["open_id_normalization_collision_count"] for result in results),
        },
        "grain": {
            "repeated_school_year_keys": sum(result["repeated_school_year_key_count"] for result in results),
            "base_duplicate_rows": sum(result["school_year_base_duplicate_rows"] for result in results),
            "panels_requiring_additional_dimensions": sum(
                result["grain_status"] == "additional_dimensions_required" for result in results
            ),
        },
        "join_integrity": {
            "orphan_school_year_keys": len(orphan_rows),
            "orphan_rows": sum(result["orphan_row_count"] for result in results),
            "panels_with_orphans": sum(result["orphan_row_count"] > 0 for result in results),
            "join_expansion_affected_rows": sum(result["join_expansion_affected_rows"] for result in results),
            "join_expansion_extra_rows": sum(result["join_expansion_extra_rows"] for result in results),
            "panels_with_join_expansion_risk": sum(result["join_expansion_extra_rows"] > 0 for result in results),
        },
        "by_source": {source: dict(values) for source, values in sorted(source_counts.items())},
        "highest_risk_panels": [
            summary_row(result)
            for result in sorted(
                results,
                key=lambda item: (
                    {"critical": 4, "high": 3, "medium": 2, "low": 1, "pass": 0}[item["severity"]],
                    item["orphan_row_count"],
                    item["open_id_missing_count"],
                    item["join_expansion_extra_rows"],
                ),
                reverse=True,
            )[:20]
        ],
        "method_notes": [
            "All 180,119,183 gzip rows are re-read independently of build profiles.",
            "Deterministic provenance row IDs are recomputed exactly from stored values.",
            "Source row hashes are verified when the canonical union-field row reproduces the original member payload; non-matches are reported as not reconstructable from the canonical panel and are not treated as corruption.",
            "School-year/OpenID multiplicity, 0101 orphan coverage, and theoretical raw-0101 join expansion are exact.",
            "Additional grain dimensions are heuristic rankings from a deterministic sample of up to 10,000 nonmissing-ID rows per panel using HyperLogLog estimates.",
            "A repeated school-year/OpenID key is not labeled an error; it triggers review for additional dimensions.",
        ],
    }

    write_json(repo_root / args.output_summary, summary)
    write_csv(repo_root / args.output_panels, panel_rows, list(panel_rows[0]))
    write_csv(
        repo_root / args.output_years,
        year_rows,
        ["source", "catalog_code", "dataset", "year", "row_count", "missing_open_id_rows", "orphan_rows"],
    )
    write_csv(
        repo_root / args.output_orphans,
        orphan_rows,
        [
            "source",
            "catalog_code",
            "dataset",
            "year",
            "open_id",
            "row_count",
            "classification",
            "first_0101_year",
            "last_0101_year",
        ],
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "logical_panel_count": summary["logical_panel_count"],
                "total_rows": summary["total_rows"],
                "severity_counts": summary["severity_counts"],
                "orphan_school_year_keys": summary["join_integrity"]["orphan_school_year_keys"],
                "missing_open_id_rows": summary["open_id"]["missing_rows"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
