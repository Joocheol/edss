#!/usr/bin/env python3
"""Convert the EDSS reference workbook into a stable, auditable CSV inventory."""

from __future__ import annotations

import argparse
import csv
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref).group(0)
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - 64
    return result - 1


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.text or "" for node in item.findall(".//m:t", NS)) for item in root.findall("m:si", NS)]


def workbook_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relationships.findall("r:Relationship", REL_NS)}
    sheets = []
    for sheet in workbook.findall("m:sheets/m:sheet", NS):
        target = targets[sheet.attrib[DOC_REL]]
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = str(Path("xl") / target)
        sheets.append((sheet.attrib["name"], path))
    return sheets


def sheet_rows(archive: zipfile.ZipFile, path: str, strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(archive.read(path))
    result: list[list[str]] = []
    for row in root.findall(".//m:sheetData/m:row", NS):
        values: dict[int, str] = {}
        for cell in row.findall("m:c", NS):
            ref = cell.attrib.get("r", "A1")
            kind = cell.attrib.get("t", "")
            if kind == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//m:t", NS))
            else:
                node = cell.find("m:v", NS)
                raw = "" if node is None else (node.text or "")
                if kind == "s" and raw:
                    value = strings[int(raw)]
                else:
                    value = raw
            values[column_index(ref)] = re.sub(r"\s+", " ", value).strip()
        if values:
            width = max(values) + 1
            result.append([values.get(index, "") for index in range(width)])
        else:
            result.append([])
    return result


def clean_header(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def priority_lookup(path: Path) -> dict[tuple[str, str], str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {(row["code"], row["dataset"]): row["priority"] for row in csv.DictReader(handle)}


def infer_unit(fields: str) -> str:
    if any(term in fields for term in ("학과명", "학과한글명", "전공명", "단과대학명")):
        return "대학·학과"
    if any(term in fields for term in ("학교명", "학교구분명", "본분교명")):
        return "대학·학교"
    return "집계 또는 기관"


def relevance(sheet_name: str, source_domain: str) -> str:
    if sheet_name.startswith(("고등교육통계", "대학정보공시")) or source_domain == "취업통계":
        return "primary"
    if source_domain == "평생교육통계":
        return "contextual"
    return "out_of_scope_initial"


def build_inventory(workbook_path: Path, priority_path: Path) -> list[dict[str, str]]:
    priorities = priority_lookup(priority_path)
    records: list[dict[str, str]] = []
    with zipfile.ZipFile(workbook_path) as archive:
        strings = shared_strings(archive)
        for sheet_name, sheet_path in workbook_sheets(archive):
            rows = sheet_rows(archive, sheet_path, strings)
            header_index = next(
                index for index, row in enumerate(rows)
                if "번호" in {clean_header(value) for value in row}
                and "제공년도" in {clean_header(value) for value in row}
            )
            headers = [clean_header(value) for value in rows[header_index]]
            carried = {key: "" for key in ("구분", "대영역", "번호", "영역", "대학구분")}
            for excel_row, values in enumerate(rows[header_index + 1 :], start=header_index + 2):
                padded = values + [""] * max(0, len(headers) - len(values))
                row = dict(zip(headers, padded))
                for key in carried:
                    if row.get(key):
                        carried[key] = row[key]
                    elif key in row:
                        row[key] = carried[key]
                code = row.get("번호", "")
                dataset = row.get("영역", "")
                fields = row.get("주요제공항목", "")
                years = row.get("제공년도", "")
                if not any((code, dataset, fields, years)):
                    continue
                source_domain = row.get("구분", "") or sheet_name.split("(", 1)[0]
                record = {
                    "catalog_sheet": sheet_name,
                    "catalog_row": str(excel_row),
                    "source_domain": source_domain,
                    "major_area": row.get("대영역", ""),
                    "code": code,
                    "dataset": dataset,
                    "university_scope": row.get("대학구분", ""),
                    "delivery_type": "EDSS file data",
                    "provider": "EDSS open data (source custodian recorded by domain)",
                    "source_url": "https://www.edmgr.kr/edss/es/opd/odd/od/es_opd_oddod01_001",
                    "available_years": years,
                    "analysis_unit": infer_unit(fields),
                    "major_fields": fields,
                    "provision_level": row.get("제공수준", ""),
                    "school_code_available": row.get("학교코드제공여부", ""),
                    "seventy_percent_rule": row.get("70%여부", ""),
                    "priority": priorities.get((code, dataset), "3"),
                    "panel_relevance": relevance(sheet_name, source_domain),
                    "download_status": "catalogued_not_downloaded",
                    "notes": row.get("비고", ""),
                }
                records.append(record)
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--priorities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = build_inventory(args.workbook, args.priorities)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    print(f"wrote {len(records)} catalog records to {args.output}")


if __name__ == "__main__":
    main()
