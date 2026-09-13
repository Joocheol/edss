#!/usr/bin/env python3
"""Build an auditable EDSS 0101 OpenID ↔ KEDI school crosswalk.

The script never edits source files.  It compares annual KEDI school-level
workbooks with the annual CSV members in the EDSS 0101 ZIP, using location,
campus, school-unit type, and independent numeric measure families.  School
names are attached only after a row match has been selected.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


KESS_DATASET_URL = (
    "https://kess.kedi.re.kr/contents/dataset?"
    "itemCode=04&menuId=m_02_04_03_02&tabId=m2"
)

SCHOOL_TYPE_MAP = {
    "대학교": "대학",
    "산업대학": "산업대학",
    "교육대학": "교육대학",
    "전문대학": "전문대학",
    "기능대학": "기능대학",
    "사이버대학(대학)": "사이버대학(대학과정)",
    "사이버대학(전문)": "사이버대학(전문대학과정)",
    "사내대학(대학)": "사내대학(대학과정)",
    "사내대학(전문)": "사내대학(전문대학과정)",
    "각종대학(대학)": "각종학교(대학과정)",
    "전공대학": "전공대학",
    "기술대학": "기술대학(대학과정)",
    "방송통신대학교": "방송통신대학",
    "원격대학(대학)": "원격대학(대학과정)",
    "원격대학(전문)": "원격대학(전문대학과정)",
}

CAMPUS_MAP = {
    "본교(제1캠퍼스)": "본교",
    "본교(제2캠퍼스)": "제2캠퍼스",
    "본교(제3캠퍼스)": "제3캠퍼스",
    "본교(제4캠퍼스)": "제4캠퍼스",
    "분교(제1캠퍼스)": "분교1",
}

METRIC_FAMILIES = {
    "enrollment": (
        ["재적생_전체_계", "재적생_전체_여"],
        ["고등교육학교_재적학생수", "고등교육학교_재적여학생수"],
        5,
    ),
    "faculty": (
        ["전임교원_계", "전임교원_여"],
        ["고등교육학교_교원수", "고등교육학교_여자교원수"],
        4,
    ),
    "entrants": (
        ["입학자_전체_계", "입학자_전체_여"],
        ["고등교육학교_입학생수", "고등교육학교_여자입학생수"],
        4,
    ),
    "graduates": (
        ["졸업자_전체_계", "졸업자_전체_여"],
        ["고등교육학교_졸업생수", "고등교육학교_여자졸업생수"],
        4,
    ),
    "staff": (
        ["직원_계", "직원_여"],
        ["고등교육학교_사무직원수", "고등교육학교_여자사무직원수"],
        3,
    ),
    "departments": (
        ["학과수_전체"],
        ["고등교육학교_학과수"],
        2,
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalized_name(value: object) -> str:
    return re.sub(r"\s+", "", clean_text(value)).replace("(폐교)", "")


def canonical_campus(value: object) -> str:
    text = clean_text(value)
    return CAMPUS_MAP.get(text, text)


def canonical_kedi_unit(row: pd.Series) -> str:
    graduate_type = clean_text(row.get("대학원구분", ""))
    if graduate_type == "부설대학원":
        return "대학원"
    if graduate_type == "대학원대학":
        return "대학원대학"
    school_type = clean_text(row.get("학제", ""))
    return SCHOOL_TYPE_MAP.get(school_type, school_type)


def canonical_edss_unit(row: pd.Series) -> str:
    school_group = clean_text(row.get("학교구분명", ""))
    if school_group in {"대학원", "대학원대학"}:
        return school_group
    return clean_text(row.get("학제유형명", ""))


def read_edss_year(archive: zipfile.ZipFile, year: int) -> tuple[pd.DataFrame, str]:
    suffix = f"({year % 100:02d}).csv"
    member = next(name for name in archive.namelist() if name.endswith(suffix))
    frame = pd.read_csv(io.BytesIO(archive.read(member)), encoding="cp949", dtype=str)
    return frame, member


def prepare_frames(kedi: pd.DataFrame, edss: pd.DataFrame) -> None:
    for kedi_cols, edss_cols, _ in METRIC_FAMILIES.values():
        for col in kedi_cols:
            kedi[col] = pd.to_numeric(kedi[col], errors="coerce").fillna(0).astype(int)
        for col in edss_cols:
            edss[col] = pd.to_numeric(edss[col], errors="coerce").fillna(0).astype(int)

    kedi["_unit"] = kedi.apply(canonical_kedi_unit, axis=1)
    edss["_unit"] = edss.apply(canonical_edss_unit, axis=1)
    kedi["_region"] = kedi["시군구"].map(clean_text)
    edss["_region"] = edss["지역명"].map(clean_text)
    kedi["_campus"] = kedi["본분교"].map(canonical_campus)
    edss["_campus"] = edss["본분교명"].map(canonical_campus)


def unique_signature_matches(
    kedi: pd.DataFrame,
    edss: pd.DataFrame,
    kedi_metrics: list[str],
    edss_metrics: list[str],
) -> list[tuple[int, int]]:
    kedi_index: dict[tuple[object, ...], list[int]] = defaultdict(list)
    edss_index: dict[tuple[object, ...], list[int]] = defaultdict(list)
    kedi_cols = ["_unit", "_region", "_campus", *kedi_metrics]
    edss_cols = ["_unit", "_region", "_campus", *edss_metrics]

    for idx, row in kedi.iterrows():
        values = tuple(row[col] for col in kedi_metrics)
        if any(int(value) != 0 for value in values):
            kedi_index[tuple(row[col] for col in kedi_cols)].append(idx)
    for idx, row in edss.iterrows():
        values = tuple(row[col] for col in edss_metrics)
        if any(int(value) != 0 for value in values):
            edss_index[tuple(row[col] for col in edss_cols)].append(idx)

    result: list[tuple[int, int]] = []
    for signature, kedi_rows in kedi_index.items():
        edss_rows = edss_index.get(signature, [])
        if len(kedi_rows) == 1 and len(edss_rows) == 1:
            result.append((kedi_rows[0], edss_rows[0]))
    return result


def match_year(kedi: pd.DataFrame, edss: pd.DataFrame) -> pd.DataFrame:
    """Return one-to-one row matches with evidence families and confidence."""
    prepare_frames(kedi, edss)
    pair_evidence: dict[tuple[int, int], set[str]] = defaultdict(set)
    pair_weight: Counter[tuple[int, int]] = Counter()

    for family, (kedi_cols, edss_cols, weight) in METRIC_FAMILIES.items():
        for pair in unique_signature_matches(kedi, edss, kedi_cols, edss_cols):
            pair_evidence[pair].add(family)
            pair_weight[pair] += weight

    candidates = []
    for (k_idx, e_idx), families in pair_evidence.items():
        if len(families) >= 2:
            confidence = "high"
        elif "enrollment" in families or "faculty" in families:
            confidence = "medium"
        else:
            confidence = "candidate"
        candidates.append(
            {
                "kedi_index": k_idx,
                "edss_index": e_idx,
                "families": ",".join(sorted(families)),
                "family_count": len(families),
                "weight": pair_weight[(k_idx, e_idx)],
                "confidence": confidence,
            }
        )

    rank = {"high": 3, "medium": 2, "candidate": 1}
    candidates.sort(
        key=lambda row: (rank[row["confidence"]], row["family_count"], row["weight"]),
        reverse=True,
    )
    chosen = []
    used_kedi: set[int] = set()
    used_edss: set[int] = set()
    for row in candidates:
        if row["kedi_index"] in used_kedi or row["edss_index"] in used_edss:
            continue
        chosen.append(row)
        used_kedi.add(row["kedi_index"])
        used_edss.add(row["edss_index"])
    return pd.DataFrame(chosen)


def select_name(group: pd.DataFrame) -> str:
    ranked = group.copy()
    campus_rank = {"본교": 0, "분교1": 1, "제2캠퍼스": 2, "제3캠퍼스": 3, "제4캠퍼스": 4}
    ranked["_campus_rank"] = ranked["kedi_campus_canonical"].map(campus_rank).fillna(9)
    ranked["_name_norm"] = ranked["kedi_school_name"].map(normalized_name)
    counts = ranked["_name_norm"].value_counts().to_dict()
    ranked["_name_frequency"] = ranked["_name_norm"].map(counts)
    ranked = ranked.sort_values(
        ["_name_frequency", "_campus_rank", "kedi_school_name"],
        ascending=[False, True, True],
    )
    return clean_text(ranked.iloc[0]["kedi_school_name"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kedi-dir", type=Path, required=True)
    parser.add_argument("--edss-zip", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--metadata-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.metadata_dir.mkdir(parents=True, exist_ok=True)

    row_evidence: list[dict[str, object]] = []
    annual_rows: list[dict[str, object]] = []
    yearly_summary: list[dict[str, object]] = []
    source_manifest: list[dict[str, object]] = []

    with zipfile.ZipFile(args.edss_zip) as archive:
        for year in range(2009, 2026):
            kedi_path = args.kedi_dir / f"{year}_kedi_higher_education_school.xlsx"
            kedi = pd.read_excel(
                kedi_path,
                sheet_name="학교별 교육통계",
                header=13,
                dtype=str,
            )
            edss, edss_member = read_edss_year(archive, year)
            matches = match_year(kedi, edss)

            source_manifest.append(
                {
                    "year": year,
                    "source_file": kedi_path.name,
                    "source_url": KESS_DATASET_URL,
                    "file_size_bytes": kedi_path.stat().st_size,
                    "sha256": sha256(kedi_path),
                    "kedi_rows": len(kedi),
                    "kedi_columns": len(kedi.columns) - 6,
                    "edss_member": edss_member,
                    "edss_rows": len(edss),
                    "edss_columns": len(edss.columns) - 6,
                }
            )

            pair_by_edss: dict[int, dict[str, object]] = {}
            candidate_by_edss: dict[int, dict[str, object]] = {}
            if not matches.empty:
                for match in matches.to_dict("records"):
                    k_idx = int(match["kedi_index"])
                    e_idx = int(match["edss_index"])
                    krow = kedi.loc[k_idx]
                    erow = edss.loc[e_idx]
                    school_code = clean_text(krow.get("학교코드", ""))
                    evidence = {
                        "year": year,
                        "openid": clean_text(erow["개방ID"]),
                        "edss_row_number": e_idx + 2,
                        "kedi_row_number": k_idx + 15,
                        "kedi_school_code": school_code,
                        "kedi_school_name": clean_text(krow["학교명"]),
                        "kedi_school_type": clean_text(krow["학제"]),
                        "kedi_graduate_type": clean_text(krow.get("대학원구분", "")),
                        "kedi_school_status": clean_text(krow.get("학교상태", "")),
                        "kedi_establishment": clean_text(krow.get("설립", "")),
                        "kedi_region": clean_text(krow["시군구"]),
                        "kedi_campus": clean_text(krow["본분교"]),
                        "kedi_campus_canonical": clean_text(krow["_campus"]),
                        "edss_school_group": clean_text(erow["학교구분명"]),
                        "edss_school_type": clean_text(erow["학제유형명"]),
                        "edss_region": clean_text(erow["지역명"]),
                        "edss_campus": clean_text(erow["본분교명"]),
                        "match_confidence": match["confidence"],
                        "match_families": match["families"],
                        "match_family_count": int(match["family_count"]),
                        "match_weight": int(match["weight"]),
                        "source_file": kedi_path.name,
                        "source_url": KESS_DATASET_URL,
                    }
                    row_evidence.append(evidence)
                    if match["confidence"] == "candidate":
                        candidate_by_edss[e_idx] = evidence
                    else:
                        pair_by_edss[e_idx] = evidence

            matched_frame = pd.DataFrame(list(pair_by_edss.values()))
            matched_by_openid = {
                openid: group for openid, group in matched_frame.groupby("openid")
            } if not matched_frame.empty else {}
            candidate_frame = pd.DataFrame(list(candidate_by_edss.values()))
            candidate_by_openid = {
                openid: group for openid, group in candidate_frame.groupby("openid")
            } if not candidate_frame.empty else {}

            for openid, group in edss.groupby("개방ID", dropna=False, sort=True):
                openid_text = clean_text(openid)
                evidence_group = matched_by_openid.get(openid_text)
                direct_name = ""
                direct_code = ""
                direct_status = "unmatched"
                direct_confidence = ""
                family_set: set[str] = set()
                evidence_rows = 0
                candidate_name = ""
                candidate_evidence_rows = 0
                kedi_types: list[str] = []
                kedi_grad_types: list[str] = []
                kedi_statuses: list[str] = []
                kedi_establishments: list[str] = []
                name_variants: list[str] = []
                if evidence_group is not None and not evidence_group.empty:
                    evidence_rows = len(evidence_group)
                    direct_name = select_name(evidence_group)
                    codes = sorted({clean_text(v) for v in evidence_group["kedi_school_code"] if clean_text(v)})
                    direct_code = " | ".join(codes)
                    confidences = set(evidence_group["match_confidence"])
                    direct_confidence = "high" if "high" in confidences else "medium"
                    direct_status = "direct"
                    for value in evidence_group["match_families"]:
                        family_set.update(filter(None, clean_text(value).split(",")))
                    kedi_types = sorted({clean_text(v) for v in evidence_group["kedi_school_type"] if clean_text(v)})
                    kedi_grad_types = sorted({clean_text(v) for v in evidence_group["kedi_graduate_type"] if clean_text(v)})
                    kedi_statuses = sorted({clean_text(v) for v in evidence_group["kedi_school_status"] if clean_text(v)})
                    kedi_establishments = sorted({clean_text(v) for v in evidence_group["kedi_establishment"] if clean_text(v)})
                    name_variants = sorted({clean_text(v) for v in evidence_group["kedi_school_name"] if clean_text(v)})
                candidate_group = candidate_by_openid.get(openid_text)
                if candidate_group is not None and not candidate_group.empty:
                    candidate_name = select_name(candidate_group)
                    candidate_evidence_rows = len(candidate_group)

                annual_rows.append(
                    {
                        "year": year,
                        "openid": openid_text,
                        "edss_school_group": " | ".join(sorted({clean_text(v) for v in group["학교구분명"] if clean_text(v)})),
                        "edss_school_type": " | ".join(sorted({clean_text(v) for v in group["학제유형명"] if clean_text(v)})),
                        "province": " | ".join(sorted({clean_text(v) for v in group["시도명"] if clean_text(v)})),
                        "regions": " | ".join(sorted({clean_text(v) for v in group["지역명"] if clean_text(v)})),
                        "campuses": " | ".join(sorted({clean_text(v) for v in group["본분교명"] if clean_text(v)})),
                        "edss_campus_rows": len(group),
                        "direct_evidence_rows": evidence_rows,
                        "direct_school_name": direct_name,
                        "direct_school_name_variants": " | ".join(name_variants),
                        "direct_kedi_school_code": direct_code,
                        "kedi_school_type": " | ".join(kedi_types),
                        "kedi_graduate_type": " | ".join(kedi_grad_types),
                        "kedi_school_status": " | ".join(kedi_statuses),
                        "kedi_establishment": " | ".join(kedi_establishments),
                        "direct_match_status": direct_status,
                        "direct_confidence": direct_confidence,
                        "candidate_school_name": candidate_name,
                        "candidate_evidence_rows": candidate_evidence_rows,
                        "match_families": ",".join(sorted(family_set)),
                        "source_file": kedi_path.name,
                        "source_url": KESS_DATASET_URL,
                    }
                )

            yearly_summary.append(
                {
                    "year": year,
                    "kedi_rows": len(kedi),
                    "edss_rows": len(edss),
                    "matched_rows": len(matches),
                    "high_rows": int((matches["confidence"] == "high").sum()) if not matches.empty else 0,
                    "medium_rows": int((matches["confidence"] == "medium").sum()) if not matches.empty else 0,
                    "candidate_rows": int((matches["confidence"] == "candidate").sum()) if not matches.empty else 0,
                    "row_match_rate_kedi": len(matches) / len(kedi) if len(kedi) else 0,
                    "row_match_rate_edss": len(matches) / len(edss) if len(edss) else 0,
                    "edss_openids": int(edss["개방ID"].nunique()),
                    "direct_openids": int(matched_frame["openid"].nunique()) if not matched_frame.empty else 0,
                    "candidate_openids": int(candidate_frame["openid"].nunique()) if not candidate_frame.empty else 0,
                }
            )

    annual = pd.DataFrame(annual_rows)
    direct = annual[annual["direct_match_status"] == "direct"].copy()

    identity_rows = []
    identity_lookup: dict[str, dict[str, object]] = {}
    for openid, group in annual.groupby("openid", sort=True):
        direct_group = group[group["direct_match_status"] == "direct"].sort_values("year")
        year_names = [
            (int(row.year), clean_text(row.direct_school_name))
            for row in direct_group.itertuples()
            if clean_text(row.direct_school_name)
        ]
        codes_2025 = sorted(
            {
                clean_text(v)
                for v in direct_group.loc[direct_group["year"] == 2025, "direct_kedi_school_code"]
                if clean_text(v)
            }
        )
        latest_name = year_names[-1][1] if year_names else ""
        name_history = " | ".join(f"{year}:{name}" for year, name in year_names)
        distinct_names = sorted({normalized_name(name) for _, name in year_names if name})
        identity_status = "unmatched"
        if len(year_names) >= 2:
            identity_status = "confirmed_multi_year"
        elif len(year_names) == 1:
            identity_status = "confirmed_single_year"
        identity = {
            "openid": openid,
            "first_edss_year": int(group["year"].min()),
            "last_edss_year": int(group["year"].max()),
            "edss_year_count": int(group["year"].nunique()),
            "direct_match_year_count": len(year_names),
            "latest_direct_school_name": latest_name,
            "kedi_school_code_2025": " | ".join(codes_2025),
            "distinct_normalized_name_count": len(distinct_names),
            "name_history": name_history,
            "identity_status": identity_status,
        }
        identity_rows.append(identity)
        identity_lookup[openid] = {**identity, "year_names": year_names}

    identities = pd.DataFrame(identity_rows)

    resolved_rows = []
    for row in annual.to_dict("records"):
        identity = identity_lookup[row["openid"]]
        row["resolved_school_name"] = row["direct_school_name"]
        row["resolved_match_method"] = "direct" if row["direct_school_name"] else ""
        row["resolved_source_year"] = row["year"] if row["direct_school_name"] else ""
        if not row["resolved_school_name"] and identity["year_names"]:
            nearest_year, nearest_name = min(
                identity["year_names"],
                key=lambda item: (abs(item[0] - int(row["year"])), -item[0]),
            )
            row["resolved_school_name"] = nearest_name
            row["resolved_match_method"] = "openid_history_inferred"
            row["resolved_source_year"] = nearest_year
        row["kedi_school_code_2025"] = identity["kedi_school_code_2025"]
        row["identity_status"] = identity["identity_status"]
        resolved_rows.append(row)
    resolved = pd.DataFrame(resolved_rows)

    resolved_path = args.output_dir / "edss_0101_kedi_crosswalk_2009_2025.csv"
    compact_path = args.output_dir / "edss_0101_kedi_crosswalk_2009_2025_compact.csv"
    identity_path = args.output_dir / "edss_0101_kedi_openid_identity_2009_2025.csv"
    identity_json_path = args.output_dir / "edss_0101_kedi_openid_identity_2009_2025.json"
    evidence_path = args.output_dir / "edss_0101_kedi_row_match_evidence_2009_2025.csv"
    resolved.to_csv(resolved_path, index=False, encoding="utf-8-sig")
    compact = resolved[
        [
            "year", "openid", "resolved_school_name", "resolved_match_method",
            "resolved_source_year", "direct_school_name", "direct_confidence",
            "candidate_school_name", "edss_school_group", "edss_school_type",
            "province", "regions", "campuses", "kedi_school_code_2025",
            "identity_status", "source_file",
        ]
    ].rename(
        columns={
            "year": "조사년도",
            "openid": "개방ID",
            "resolved_school_name": "최종학교명",
            "resolved_match_method": "최종매칭방법",
            "resolved_source_year": "최종근거연도",
            "direct_school_name": "당해연도_직접학교명",
            "direct_confidence": "직접신뢰도",
            "candidate_school_name": "후보학교명",
            "edss_school_group": "EDSS_학교구분",
            "edss_school_type": "EDSS_학제유형",
            "province": "시도",
            "regions": "지역목록",
            "campuses": "캠퍼스목록",
            "kedi_school_code_2025": "2025_KEDI학교코드",
            "identity_status": "ID_통합상태",
            "source_file": "KEDI_원본파일",
        }
    )
    compact.to_csv(compact_path, index=False, encoding="utf-8-sig")
    identities.to_csv(identity_path, index=False, encoding="utf-8-sig")
    identity_json_path.write_text(
        json.dumps(identities.fillna("").to_dict("records"), ensure_ascii=False),
        encoding="utf-8",
    )
    pd.DataFrame(row_evidence).to_csv(evidence_path, index=False, encoding="utf-8-sig")

    resolved_count = int((resolved["resolved_school_name"] != "").sum())
    direct_count = int((resolved["direct_school_name"] != "").sum())
    summary = {
        "scope": "EDSS 0101 and KEDI higher-education school-level data, 2009-2025",
        "grain": "year + EDSS OpenID",
        "source_url": KESS_DATASET_URL,
        "edss_zip": args.edss_zip.name,
        "edss_zip_sha256": sha256(args.edss_zip),
        "annual_crosswalk_rows": len(resolved),
        "distinct_openids": int(resolved["openid"].nunique()),
        "directly_named_rows": direct_count,
        "directly_named_rate": direct_count / len(resolved) if len(resolved) else 0,
        "resolved_named_rows": resolved_count,
        "resolved_named_rate": resolved_count / len(resolved) if len(resolved) else 0,
        "directly_named_openids": int(direct["openid"].nunique()),
        "unresolved_openids": int((identities["identity_status"] == "unmatched").sum()),
        "openids_with_2025_kedi_school_code": int((identities["kedi_school_code_2025"] != "").sum()),
        "yearly": yearly_summary,
        "outputs": {
            "crosswalk_csv": resolved_path.name,
            "crosswalk_compact_csv": compact_path.name,
            "identity_csv": identity_path.name,
            "identity_json": identity_json_path.name,
            "row_evidence_csv": evidence_path.name,
        },
        "caveats": [
            "KEDI 2009-2024 public school-level workbooks do not contain 학교코드; 2025 does.",
            "direct matches are name-blind and use unique numeric signatures within school unit, region, and campus.",
            "openid_history_inferred rows copy the nearest direct name for the same OpenID and are not same-year direct matches.",
            "School-name changes and mergers require separate historical review before treating a name as legally continuous.",
        ],
    }
    (args.metadata_dir / "edss_0101_kedi_crosswalk_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.metadata_dir / "kedi_higher_education_school_manifest.json").write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
