#!/usr/bin/env python3
"""Build EDSS and public-data source inventories from captured official metadata."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


def norm(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edss-list", type=Path, required=True)
    parser.add_argument("--priorities", type=Path, required=True)
    parser.add_argument("--file-specs", type=Path, required=True)
    parser.add_argument("--api-specs", type=Path, required=True)
    parser.add_argument("--edss-config", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()

    edss_rows = json.loads(args.edss_list.read_text(encoding="utf-8"))["aplList"]
    priorities = list(csv.DictReader(args.priorities.open(encoding="utf-8-sig")))
    file_specs = json.loads(args.file_specs.read_text(encoding="utf-8"))
    api_specs = json.loads(args.api_specs.read_text(encoding="utf-8"))

    selected: list[dict] = []
    for priority in priorities:
        exact = [
            row
            for row in edss_rows
            if row.get("eduDataSeNm") == priority["source"]
            and norm(row.get("domnNmOri", "")) == norm(priority["dataset"])
        ]
        if not exact:
            exact = [
                row
                for row in edss_rows
                if row.get("eduDataSeNm") == priority["source"]
                and norm(priority["dataset"]) in norm(row.get("domnNmOri", ""))
            ]
        for row in exact:
            selected.append(
                {
                    "source": priority["source"],
                    "catalog_code": priority["code"],
                    "dataset": priority["dataset"],
                    "official_dataset_name": row.get("domnNmOri", ""),
                    "domn_code": row.get("domnCd", ""),
                    "major_area": row.get("ldomnNm", ""),
                    "advertised_years": row.get("pvsnYrNm", "").replace("\r\n", " "),
                    "priority": int(priority["priority"]),
                    "purpose": priority["purpose"],
                    "major_fields": row.get("pvsnArtclNmOri", ""),
                    "source_url": "https://www.edmgr.kr/edss/es/opd/odd/od/es_opd_oddod01_001",
                    "provider": "교육부·한국교육학술정보원 EDSS",
                    "delivery": "file",
                    "license": "EDSS 다운로드 정책 확인 필요",
                }
            )

    args.edss_config.parent.mkdir(parents=True, exist_ok=True)
    args.edss_config.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    inventory: list[dict] = []
    for row in selected:
        inventory.append(
            {
                "source_group": "EDSS",
                "dataset_id": row["domn_code"],
                "dataset_name": row["official_dataset_name"],
                "provider": row["provider"],
                "delivery_type": "파일데이터",
                "official_url": row["source_url"],
                "reference_years": row["advertised_years"],
                "unit": "원본 필드 및 행 수준 확인 필요",
                "major_fields": row["major_fields"],
                "priority": row["priority"],
                "format_or_endpoint": "공식 연도별/전체 파일",
                "modified": "",
                "license": row["license"],
                "download_status": "연도별 파일 목록 확인; 다운로드 엔드포인트 실측 대상",
                "notes": f"catalog_code={row['catalog_code']}; domnCd={row['domn_code']}",
            }
        )

    for row in file_specs:
        status = "직접 다운로드 가능"
        if row["id"] == "15081503":
            status = "외부 제공처 입력 양식 필요; 현재 2024~2026 항목 확인"
        elif row["id"] == "15139338":
            status = "외부 제공처 첨부파일 다운로드 가능"
        inventory.append(
            {
                "source_group": "공공데이터포털 검색결과 파일데이터",
                "dataset_id": row["id"],
                "dataset_name": row["name"],
                "provider": row["provider"],
                "delivery_type": "파일데이터",
                "official_url": row["source_url"],
                "reference_years": row.get("reference_years", ""),
                "unit": row.get("unit", ""),
                "major_fields": row.get("major_fields", ""),
                "priority": row.get("priority", ""),
                "format_or_endpoint": row.get("format", ""),
                "modified": row.get("modified", ""),
                "license": row.get("license", ""),
                "download_status": status,
                "notes": "검색어 결과에는 유사 기관·교육부·한국장학재단 자료가 포함됨",
            }
        )

    for row in api_specs:
        operations = row.get("operations", [])
        paths = "; ".join(op.get("path", "") for op in operations)
        fields = sorted(
            {
                field.rsplit(".", 1)[-1]
                for op in operations
                for field in op.get("response_fields", [])
                if ".item." in field
            }
        )
        inventory.append(
            {
                "source_group": "공공데이터포털 검색결과 Open API",
                "dataset_id": row["id"],
                "dataset_name": row.get("title") or row.get("name") or row.get("key", ""),
                "provider": row.get("provider", "한국대학교육협의회"),
                "delivery_type": "Open API",
                "official_url": row.get("source_url", f"https://www.data.go.kr/data/{row['id']}/openapi.do"),
                "reference_years": "요청 파라미터 및 실제 응답으로 확인",
                "unit": "학교/학과/지역 비교통계(기능별 상이)",
                "major_fields": ", ".join(fields),
                "priority": 1 if row.get("key") in {"student", "basic_information", "education_condition", "finances"} else 2,
                "format_or_endpoint": f"{row.get('format', '')}; {row.get('base_url', '')}; {paths}",
                "modified": row.get("date_modified", ""),
                "license": row.get("license", ""),
                "download_status": "공식 상세·Swagger 확인; 인증키 필요",
                "notes": f"operations={len(operations)}; daily_traffic={row.get('traffic', '')}",
            }
        )

    fields = [
        "source_group",
        "dataset_id",
        "dataset_name",
        "provider",
        "delivery_type",
        "official_url",
        "reference_years",
        "unit",
        "major_fields",
        "priority",
        "format_or_endpoint",
        "modified",
        "license",
        "download_status",
        "notes",
    ]
    write_csv(args.inventory, inventory, fields)
    print(f"wrote {len(selected)} EDSS priority mappings and {len(inventory)} inventory rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
