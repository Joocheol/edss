#!/usr/bin/env python3
"""Build the canonical input inventory for the full EDSS panel rebuild.

The live catalog defines 265 physical ``domnCd`` download units, while archive
provenance is split between the original priority manifest and the later full
collection attempt log.  This script reconciles those sources without touching
raw ZIPs and records how much of the full collection the existing priority
panel configuration covers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


INVENTORY_FIELDS = [
    "logical_table_key",
    "source",
    "catalog_code",
    "dataset",
    "major_area",
    "advertised_years",
    "domn_code",
    "status",
    "archive_count",
    "archive_bytes",
    "archive_years",
    "archive_paths",
    "archive_sha256s",
    "current_pipeline_physical_unit",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def logical_key(source: str, catalog_code: str, dataset: str) -> str:
    return f"{source}|{catalog_code}|{dataset}"


def build_inventory(
    targets: list[dict],
    status_rows: list[dict],
    existing_manifest: list[dict],
    attempts: list[dict],
    current_config: list[dict],
    repo_root: Path,
    *,
    verify_files: bool = True,
    verify_sha256: bool = False,
) -> tuple[list[dict], dict]:
    """Return one inventory row per live physical unit and a quality summary."""

    issues: list[dict] = []

    def issue(kind: str, message: str, **context: object) -> None:
        issues.append({"severity": "critical", "type": kind, "message": message, **context})

    target_by_domn: dict[str, dict] = {}
    for target in targets:
        domn_code = str(target.get("domn_code", ""))
        if not domn_code:
            issue("target_missing_domn_code", "Live target has no domn_code", target=target)
            continue
        if domn_code in target_by_domn:
            issue("duplicate_target_domn_code", "Live target domn_code is not unique", domn_code=domn_code)
            continue
        target_by_domn[domn_code] = target

    status_by_domn: dict[str, dict] = {}
    for row in status_rows:
        domn_code = str(row.get("domn_code", ""))
        if domn_code in status_by_domn:
            issue("duplicate_status_domn_code", "Status domn_code is not unique", domn_code=domn_code)
            continue
        status_by_domn[domn_code] = row

    archive_by_domn: dict[str, list[dict]] = defaultdict(list)
    for record in existing_manifest + attempts:
        if record.get("status") == "downloaded":
            archive_by_domn[str(record.get("domn_code", ""))].append(record)

    target_codes = set(target_by_domn)
    for domn_code in sorted(set(status_by_domn) - target_codes):
        issue("orphan_status_domn_code", "Status row is not present in the live catalog", domn_code=domn_code)
    for domn_code in sorted(set(archive_by_domn) - target_codes):
        issue("orphan_archive_domn_code", "Downloaded archive is not present in the live catalog", domn_code=domn_code)

    current_by_domn = {str(row.get("domn_code", "")): row for row in current_config}
    verified_file_count = 0
    verified_sha256_count = 0
    inventory: list[dict] = []

    for domn_code, target in target_by_domn.items():
        source = str(target.get("source", ""))
        dataset = str(target.get("dataset", ""))
        status = status_by_domn.get(domn_code)
        records = sorted(
            archive_by_domn.get(domn_code, []),
            key=lambda row: (str(row.get("file_year", "")), str(row.get("local_path", ""))),
        )

        if status is None:
            issue("missing_status", "Live target has no status row", source=source, dataset=dataset, domn_code=domn_code)
            status_value = "missing"
        else:
            status_value = str(status.get("status", ""))
            if status_value != "downloaded":
                issue(
                    "target_not_downloaded",
                    "Live target is not marked downloaded",
                    source=source,
                    dataset=dataset,
                    domn_code=domn_code,
                    status=status_value,
                )

        if not records:
            issue("missing_archive_record", "Live target has no downloaded archive record", source=source, dataset=dataset, domn_code=domn_code)

        catalog_codes = {str(record.get("catalog_code", "")) for record in records if record.get("catalog_code")}
        catalog_code = next(iter(catalog_codes)) if len(catalog_codes) == 1 else ""
        if len(catalog_codes) != 1:
            issue(
                "catalog_code_not_resolved",
                "Archive records do not resolve to exactly one catalog code",
                source=source,
                dataset=dataset,
                domn_code=domn_code,
                catalog_codes=sorted(catalog_codes),
            )

        archive_bytes = 0
        for record in records:
            archive_path_text = str(record.get("local_path", ""))
            archive_path = Path(archive_path_text)
            if not archive_path.is_absolute():
                archive_path = repo_root / archive_path
            expected_size = int(record.get("size_bytes", 0) or 0)
            archive_bytes += expected_size

            if str(record.get("source", "")) != source or str(record.get("dataset", "")) != dataset:
                issue(
                    "archive_target_mismatch",
                    "Archive source or dataset does not match the live target",
                    source=source,
                    dataset=dataset,
                    domn_code=domn_code,
                    archive_path=archive_path_text,
                    archive_source=record.get("source", ""),
                    archive_dataset=record.get("dataset", ""),
                )
            if verify_files:
                if not archive_path.is_file():
                    issue(
                        "archive_file_missing",
                        "Archive path does not exist",
                        source=source,
                        dataset=dataset,
                        domn_code=domn_code,
                        archive_path=archive_path_text,
                    )
                    continue
                actual_size = archive_path.stat().st_size
                if actual_size != expected_size:
                    issue(
                        "archive_size_mismatch",
                        "Archive size does not match its provenance record",
                        source=source,
                        dataset=dataset,
                        domn_code=domn_code,
                        archive_path=archive_path_text,
                        expected_size=expected_size,
                        actual_size=actual_size,
                    )
                    continue
                verified_file_count += 1
                if verify_sha256:
                    actual_sha256 = sha256_file(archive_path)
                    if actual_sha256 != str(record.get("sha256", "")):
                        issue(
                            "archive_sha256_mismatch",
                            "Archive SHA-256 does not match its provenance record",
                            source=source,
                            dataset=dataset,
                            domn_code=domn_code,
                            archive_path=archive_path_text,
                        )
                    else:
                        verified_sha256_count += 1

        if status is not None:
            expected_archive_count = int(status.get("archive_count", 0) or 0)
            expected_bytes = int(status.get("size_bytes", 0) or 0)
            if expected_archive_count != len(records):
                issue(
                    "status_archive_count_mismatch",
                    "Status archive_count does not match provenance records",
                    source=source,
                    dataset=dataset,
                    domn_code=domn_code,
                    status_archive_count=expected_archive_count,
                    provenance_archive_count=len(records),
                )
            if expected_bytes != archive_bytes:
                issue(
                    "status_archive_bytes_mismatch",
                    "Status size_bytes does not match provenance records",
                    source=source,
                    dataset=dataset,
                    domn_code=domn_code,
                    status_bytes=expected_bytes,
                    provenance_bytes=archive_bytes,
                )

        current_entry = current_by_domn.get(domn_code)
        if current_entry and (
            str(current_entry.get("source", "")) != source or str(current_entry.get("dataset", "")) != dataset
        ):
            issue(
                "current_pipeline_target_mismatch",
                "Current pipeline config maps domn_code to a different table",
                source=source,
                dataset=dataset,
                domn_code=domn_code,
            )

        inventory.append(
            {
                "logical_table_key": logical_key(source, catalog_code, dataset),
                "source": source,
                "catalog_code": catalog_code,
                "dataset": dataset,
                "major_area": str(target.get("major_area", "")),
                "advertised_years": str(target.get("advertised_years", "")),
                "domn_code": domn_code,
                "status": status_value,
                "archive_count": len(records),
                "archive_bytes": archive_bytes,
                "archive_years": json.dumps([str(record.get("file_year", "")) for record in records], ensure_ascii=False),
                "archive_paths": json.dumps([str(record.get("local_path", "")) for record in records], ensure_ascii=False),
                "archive_sha256s": json.dumps([str(record.get("sha256", "")) for record in records], ensure_ascii=False),
                "current_pipeline_physical_unit": "yes" if current_entry else "no",
            }
        )

    logical_groups: dict[str, list[dict]] = defaultdict(list)
    for row in inventory:
        logical_groups[row["logical_table_key"]].append(row)

    current_rows = [row for row in inventory if row["current_pipeline_physical_unit"] == "yes"]
    new_rows = [row for row in inventory if row["current_pipeline_physical_unit"] == "no"]
    current_logical_keys = {row["logical_table_key"] for row in current_rows}
    new_logical_keys = set(logical_groups) - current_logical_keys

    by_source = {}
    for source in sorted({row["source"] for row in inventory}):
        source_rows = [row for row in inventory if row["source"] == source]
        by_source[source] = {
            "logical_table_count": len({row["logical_table_key"] for row in source_rows}),
            "physical_unit_count": len(source_rows),
            "archive_count": sum(int(row["archive_count"]) for row in source_rows),
            "archive_bytes": sum(int(row["archive_bytes"]) for row in source_rows),
        }

    summary = {
        "generated_at": utc_now(),
        "grain": "one row per live EDSS physical domn_code; logical tables group by source, catalog_code, and dataset",
        "logical_table_count": len(logical_groups),
        "physical_unit_count": len(inventory),
        "archive_count": sum(int(row["archive_count"]) for row in inventory),
        "archive_bytes": sum(int(row["archive_bytes"]) for row in inventory),
        "multi_physical_logical_table_count": sum(len(rows) > 1 for rows in logical_groups.values()),
        "by_source": by_source,
        "current_pipeline": {
            "logical_table_count": len(current_logical_keys),
            "physical_unit_count": len(current_rows),
            "archive_count": sum(int(row["archive_count"]) for row in current_rows),
        },
        "not_in_current_pipeline": {
            "logical_table_count": len(new_logical_keys),
            "physical_unit_count": len(new_rows),
            "archive_count": sum(int(row["archive_count"]) for row in new_rows),
        },
        "verification": {
            "file_check_enabled": verify_files,
            "sha256_check_enabled": verify_sha256,
            "verified_file_count": verified_file_count,
            "verified_sha256_count": verified_sha256_count,
            "issue_count": len(issues),
            "issue_counts_by_type": dict(sorted(Counter(item["type"] for item in issues).items())),
            "complete": not issues and all(row["status"] == "downloaded" for row in inventory),
        },
        "issues": issues,
    }
    return inventory, summary


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def atomic_write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INVENTORY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets", type=Path, default=Path("data/metadata/edss_live_download_catalog.jsonl"))
    parser.add_argument("--status", type=Path, default=Path("data/metadata/edss_full_collection_status.csv"))
    parser.add_argument("--existing-manifest", type=Path, default=Path("data/metadata/edss_file_manifest.jsonl"))
    parser.add_argument("--attempts", type=Path, default=Path("data/metadata/edss_full_collection_attempts.jsonl"))
    parser.add_argument("--current-config", type=Path, default=Path("config/edss_priority_datasets.json"))
    parser.add_argument("--inventory-csv", type=Path, default=Path("data/metadata/edss_full_rebuild_inventory.csv"))
    parser.add_argument("--summary", type=Path, default=Path("data/metadata/edss_full_rebuild_inventory_summary.json"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--skip-file-check", action="store_true")
    parser.add_argument("--verify-sha256", action="store_true")
    args = parser.parse_args()

    current_config = json.loads(args.current_config.read_text(encoding="utf-8"))
    inventory, summary = build_inventory(
        read_jsonl(args.targets),
        read_csv(args.status),
        read_jsonl(args.existing_manifest),
        read_jsonl(args.attempts),
        current_config,
        args.repo_root,
        verify_files=not args.skip_file_check,
        verify_sha256=args.verify_sha256,
    )
    atomic_write_csv(args.inventory_csv, inventory)
    atomic_write_json(args.summary, summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "issues"}, ensure_ascii=False))
    return 0 if summary["verification"]["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
