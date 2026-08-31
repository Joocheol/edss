#!/usr/bin/env python3
"""Expand the official EDSS item list into a conservative field dictionary."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-list", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = json.loads(args.official_list.read_text(encoding="utf-8"))["aplList"]
    dictionary = []
    seen = set()
    for row in rows:
        for original in (row.get("pvsnArtclNmOri") or "").split(","):
            original = original.strip()
            if not original:
                continue
            key = (row.get("domnCd", ""), original)
            if key in seen:
                continue
            seen.add(key)
            dictionary.append(
                {
                    "source": row.get("eduDataSeNm", ""),
                    "major_area": row.get("ldomnNm", ""),
                    "domn_code": row.get("domnCd", ""),
                    "dataset": row.get("domnNmOri", ""),
                    "advertised_years": (row.get("pvsnYrNm") or "").replace("\r\n", " "),
                    "original_field": original,
                    "korean_meaning": original,
                    "data_type": "원본 파일 확인 필요",
                    "unit": "원본 파일·설명서 확인 필요",
                    "missing_value_definition": "원본 파일·설명서 확인 필요",
                    "definition_status": "공식 목록의 항목명만 확인; 의미·형식·단위 미확정",
                }
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dictionary[0]) if dictionary else []
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(dictionary)
    print(f"wrote {len(dictionary)} EDSS field records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
