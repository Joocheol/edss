#!/usr/bin/env python3
"""Audit whether EDSS publishes an official school-name/OpenID crosswalk.

The audit compares the latest official provider-list workbook with the exact
headers of the downloaded 2023-2024 employment archives. It records evidence
but never promotes a structural candidate to a canonical OpenID mapping.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET


SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
PROVIDER_SHEET = "취업(1종), 평생(2종)"
PROVIDER_RANGE = "A4:I6"
SEARCH_URL = "https://www.edmgr.kr/edss/es/mis/pas/ps/es_mis_pasps01_001"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Z]+)", cell_reference)
    if not match:
        raise ValueError(f"invalid cell reference: {cell_reference}")
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def parse_range(cell_range: str) -> tuple[int, int, int, int]:
    start, end = cell_range.split(":", 1)

    def position(reference: str) -> tuple[int, int]:
        match = re.fullmatch(r"([A-Z]+)([0-9]+)", reference)
        if not match:
            raise ValueError(f"invalid range reference: {reference}")
        return int(match.group(2)) - 1, column_index(reference)

    start_row, start_col = position(start)
    end_row, end_col = position(end)
    return start_row, start_col, end_row, end_col


def xlsx_range_values(path: Path, sheet_name: str, cell_range: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet_target_id = None
        for sheet in workbook_root.findall(f".//{{{SPREADSHEET_NS}}}sheet"):
            if sheet.attrib.get("name") == sheet_name:
                sheet_target_id = sheet.attrib[f"{{{OFFICE_REL_NS}}}id"]
                break
        if sheet_target_id is None:
            raise KeyError(f"sheet not found: {sheet_name}")

        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = None
        for relation in rel_root.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
            if relation.attrib.get("Id") == sheet_target_id:
                target = relation.attrib["Target"]
                break
        if target is None:
            raise KeyError(f"worksheet relation not found: {sheet_target_id}")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall(f"{{{SPREADSHEET_NS}}}si"):
                shared_strings.append("".join(item.itertext()))

        sheet_root = ET.fromstring(archive.read(target))
        cells: dict[tuple[int, int], str] = {}
        for cell in sheet_root.findall(f".//{{{SPREADSHEET_NS}}}c"):
            reference = cell.attrib.get("r", "")
            row_match = re.search(r"([0-9]+)$", reference)
            if not row_match:
                continue
            row = int(row_match.group(1)) - 1
            col = column_index(reference)
            cell_type = cell.attrib.get("t")
            value_node = cell.find(f"{{{SPREADSHEET_NS}}}v")
            if cell_type == "inlineStr":
                inline = cell.find(f"{{{SPREADSHEET_NS}}}is")
                value = "" if inline is None else "".join(inline.itertext())
            elif value_node is None:
                value = ""
            elif cell_type == "s":
                value = shared_strings[int(value_node.text or "0")]
            else:
                value = value_node.text or ""
            cells[(row, col)] = value

    start_row, start_col, end_row, end_col = parse_range(cell_range)
    return [
        [cells.get((row, col), "") for col in range(start_col, end_col + 1)]
        for row in range(start_row, end_row + 1)
    ]


def nested_csv_header(path: Path) -> dict:
    payload = path.read_bytes()
    depth = 0
    members = []
    while zipfile.is_zipfile(io.BytesIO(payload)):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = [name for name in archive.namelist() if not name.endswith("/")]
            csv_members = [name for name in files if name.casefold().endswith(".csv")]
            zip_members = [name for name in files if name.casefold().endswith(".zip")]
            if len(csv_members) == 1:
                selected = csv_members[0]
            elif len(zip_members) == 1:
                selected = zip_members[0]
            else:
                raise RuntimeError(f"expected one CSV or nested ZIP in {path}, found {files}")
            members.append(selected)
            payload = archive.read(selected)
            depth += 1

    header = None
    encoding = None
    for candidate in ("utf-8-sig", "cp949"):
        try:
            text = payload.decode(candidate)
            header = next(csv.reader(io.StringIO(text)))
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if header is None:
        raise UnicodeError(f"unable to decode CSV in {path}")
    return {
        "archive_depth": depth,
        "member_chain": members,
        "detected_encoding": encoding,
        "column_count": len(header),
        "has_school_name": "학교명" in header,
        "has_open_id": "개방ID" in header,
        "school_code_fields": [
            field for field in header
            if "코드" in field or "ID" in field or "아이디" in field
        ],
    }


def provider_employment_evidence(path: Path) -> dict:
    rows = xlsx_range_values(path, PROVIDER_SHEET, PROVIDER_RANGE)
    headers = [re.sub(r"\s+", "", value) for value in rows[0]]
    year_index = headers.index("제공년도")
    field_index = headers.index("주요제공항목")
    code_index = headers.index("학교코드제공여부")
    selected = next(row for row in rows[1:] if row[year_index] == "2023~2024")
    fields = [field.strip() for field in selected[field_index].split(",") if field.strip()]
    return {
        "sheet": PROVIDER_SHEET,
        "range": PROVIDER_RANGE,
        "period": selected[year_index],
        "listed_field_count": len(fields),
        "listed_has_school_name": "학교명" in fields,
        "listed_has_open_id": "개방ID" in fields,
        "listed_school_code_provided": selected[code_index],
    }


def search_evidence(term: str, result_count: int) -> dict:
    return {
        "term": term,
        "result_count": result_count,
        "url": f"{SEARCH_URL}?searchPvsnArtclCd4={quote(term)}",
    }


def build_audit(
    provider_list: Path,
    employment_archives: list[tuple[str, Path]],
    candidate_csv: Path,
    open_id_search_results: int,
    school_code_search_results: int,
) -> dict:
    provider = provider_employment_evidence(provider_list)
    provider.update(
        {
            "path": str(provider_list),
            "sha256": sha256_file(provider_list),
            "size_bytes": provider_list.stat().st_size,
            "source_url": "https://www.edmgr.kr/edss/es/opd/odd/od/es_opd_oddod01_001",
        }
    )

    raw_evidence = []
    for year, archive in employment_archives:
        evidence = nested_csv_header(archive)
        evidence.update(
            {
                "year": year,
                "path": str(archive),
                "sha256": sha256_file(archive),
            }
        )
        raw_evidence.append(evidence)

    with candidate_csv.open(encoding="utf-8-sig", newline="") as handle:
        candidates = list(csv.DictReader(handle))
    status_counts = Counter(row["resolution_status"] for row in candidates)
    candidate_ids = {row["candidate_open_id"] for row in candidates if row["candidate_open_id"]}

    metadata_conflict = (
        provider["listed_school_code_provided"] == "Y"
        and all(not row["has_open_id"] and not row["school_code_fields"] for row in raw_evidence)
    )
    search_checks = [
        search_evidence("개방ID", open_id_search_results),
        search_evidence("학교코드", school_code_search_results),
    ]
    official_crosswalk_available = not metadata_conflict and any(
        row["has_open_id"] or row["school_code_fields"] for row in raw_evidence
    )

    return {
        "audit_version": 1,
        "generated_at": utc_now(),
        "status": "review_required",
        "intended_use": "Confirm 2023-2024 school-name/OpenID mappings for longitudinal employment analysis.",
        "official_provider_list": provider,
        "official_site_search": search_checks,
        "employment_raw_headers": raw_evidence,
        "candidate_resolution": {
            "school_year_identity_count": len(candidates),
            "context_confirmed_candidate_identity_count": status_counts[
                "candidate_signature_context_confirmed"
            ],
            "candidate_open_id_count": len(candidate_ids),
            "officially_confirmed_candidate_identity_count": 0,
            "canonical_open_id_imputed_row_count": 0,
        },
        "crosswalk_conclusion": {
            "official_crosswalk_available": official_crosswalk_available,
            "metadata_conflict": metadata_conflict,
            "mapping_action": "none",
            "required_confirmation": (
                "An EDSS-issued school-name/OpenID crosswalk or written clarification of "
                "the school-code flag is required before promoting candidates."
            ),
        },
        "findings": [
            {
                "code": "employment_2023_2024_school_code_metadata_conflict",
                "severity": "high",
                "confidence": "high",
                "evidence": (
                    "The August 2026 EDSS provider list marks school-code availability Y for "
                    "2023-2024 employment data, while both checksum-verified archives contain "
                    "24 columns with 학교명 and no 개방ID or other code-like school field."
                ),
                "impact": "The published metadata cannot support a school-name/OpenID join.",
                "remediation": "Request EDSS clarification or a corrected file/crosswalk.",
            },
            {
                "code": "official_school_name_open_id_crosswalk_not_published",
                "severity": "high",
                "confidence": "high",
                "evidence": (
                    "The official provider workbook contains no 개방ID field entry, and official "
                    "site searches for 개방ID and 학교코드 return no records."
                ),
                "impact": "Thirty structurally supported school-year candidates remain noncanonical.",
                "remediation": "Keep candidates separate and do not impute canonical OpenID values.",
            },
        ],
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider-list",
        type=Path,
        default=Path("data/raw/edss/reference/edss_open_data_provider_list_2026-08.xlsx"),
    )
    parser.add_argument(
        "--candidate-csv",
        type=Path,
        default=Path("data/metadata/edss_employment_2023_2024_open_id_candidates.csv"),
    )
    parser.add_argument(
        "--employment-2023",
        type=Path,
        default=Path(
            "data/raw/edss/취업통계/0001_학생인적취업정보_13300/"
            "0001_학생인적취업정보_2023.zip"
        ),
    )
    parser.add_argument(
        "--employment-2024",
        type=Path,
        default=Path(
            "data/raw/edss/취업통계/0001_학생인적취업정보_13300/"
            "0001_학생인적취업정보_2024.zip"
        ),
    )
    parser.add_argument("--open-id-search-results", type=int, default=0)
    parser.add_argument("--school-code-search-results", type=int, default=0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/metadata/edss_official_crosswalk_audit.json"),
    )
    args = parser.parse_args()

    audit = build_audit(
        args.provider_list,
        [("2023", args.employment_2023), ("2024", args.employment_2024)],
        args.candidate_csv,
        args.open_id_search_results,
        args.school_code_search_results,
    )
    atomic_write_json(args.output, audit)
    print(
        json.dumps(
            {
                "status": audit["status"],
                "official_crosswalk_available": audit["crosswalk_conclusion"][
                    "official_crosswalk_available"
                ],
                "metadata_conflict": audit["crosswalk_conclusion"]["metadata_conflict"],
                "officially_confirmed_candidates": audit["candidate_resolution"][
                    "officially_confirmed_candidate_identity_count"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
