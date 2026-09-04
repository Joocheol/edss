#!/usr/bin/env python3
"""Review every unmatched EDSS 0101 OpenID that disappeared before 2025.

The output is intentionally conservative: a likely historical school name is
recorded as evidence, but no OpenID is automatically merged with another ID.
Raw EDSS and KEDI inputs are read only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb


DEFAULT_DUCKDB = Path(
    "/Users/joocheol/Documents/GitHub/edss/data/processed/edss/restricted/edss_all.duckdb"
)
OUTPUT_FIELDS = (
    "review_order",
    "open_id",
    "first_year",
    "last_0101_year",
    "year_count",
    "latest_any_panel_year",
    "identity_status",
    "manual_classification",
    "likely_entity_candidate",
    "candidate_status",
    "candidate_evidence_years",
    "candidate_kedi_statuses",
    "latest_panel_count",
    "latest_row_count",
    "latest_nonzero_measure_count",
    "latest_department_status",
    "department_examples",
    "safe_join_action",
    "evidence_summary",
)

# These eight records had no machine-selected name after the first pass.  Each
# override is supported by the same-year KEDI row plus location and department
# context from EDSS.  They remain candidates and are never auto-joined.
CONTEXT_OVERRIDES = {
    "1139362752": ("한양대학교 도시융합개발대학원", "2014", "existing_department_context"),
    "2031110882": ("남부대학교 태권도체육대학원", "2009", "all_zero_closed_kedi_context"),
    "4062185227": ("영산대학교 경영대학원", "2009", "all_zero_closed_duplicate_kedi_context"),
    "4468261480": ("협성대학교 행정대학원", "2013|2014|2015", "all_zero_existing_department_context"),
    "5853597760": ("밀양대학교산업대학원", "2009", "all_zero_closed_kedi_context"),
    "6058101713": ("협성대학교 경영대학원", "2013|2014|2015", "all_zero_existing_department_context"),
    "7638497352": ("부산대학교 국제대학원", "2009|2010|2011", "all_zero_closed_kedi_context"),
    "8947065591": ("한북대학교 경영대학원", "2011", "staff_and_department_context"),
}

FINAL_REVIEW_IDS = {
    "1139362752",
    "2031110882",
    "2118588984",
    "2545302689",
    "3042672216",
    "3194591462",
    "4062185227",
    "4080095410",
    "4468261480",
    "5853597760",
    "6058101713",
    "6505783319",
    "7470429599",
    "7638497352",
    "8947065591",
    "9206062912",
}

CONFIRMED_IDENTITY_OVERRIDES = {
    "9032066404": {
        "name": "인천대학교산업대학원",
        "years": "2009",
        "candidate_status": "confirmed_exact_metric_and_official_history",
        "classification": "confirmed_identity_name_change_predecessor_id",
        "kedi_statuses": "기존|2009-06-26 공학대학원으로 명칭변경",
        "safe_action": "link_successor_open_id_8369060277_keep_predecessor_id_separate",
        "summary": (
            "2009년 EDSS의 재적 62명·입학 35명·졸업 29명·학과 8개와 성별 세부값이 "
            "KEDI 인천대학교산업대학원 행에 정확히 일치한다. EDSS는 당시 행정구역명인 "
            "인천 남구, KEDI 파일은 인천 미추홀구를 사용해 자동 매칭이 실패했다. "
            "인천대학교 공식 연혁의 2009-06-26 산업대학원→공학대학원 명칭변경과 "
            "2010년부터의 후속 OpenID 8369060277을 연결하되 두 ID는 별도 보존한다."
        ),
    },
    "9263845205": {
        "name": "우송대학교정보산업대학원",
        "years": "2009",
        "candidate_status": "confirmed_residual_metric_department_and_official_history",
        "classification": "confirmed_identity_closed_name_change_predecessor_id",
        "kedi_statuses": "폐교|공학대학원·공학디자인대학원으로 명칭변경",
        "safe_action": "retain_predecessor_open_id_separately_no_unique_successor_auto_join",
        "summary": (
            "2009년 EDSS에서 졸업생 1명만 비영이고 나머지 0101 핵심 지표는 0이며, "
            "건설환경공학과·식품생명공학과·컴퓨터전자공학과가 모두 폐과다. 같은 지역 "
            "KEDI의 우송대학교정보산업대학원(폐교)도 졸업생 1명만 비영이다. 우송대학교 "
            "공식 연혁에서 정보산업대학원→공학대학원→공학·디자인대학원 변경을 확인했다. "
            "후속 명칭에 EDSS ID가 둘 이상이므로 특정 후속 ID와 자동 조인하지 않는다."
        ),
    },
    "1651128768": {
        "name": "한국폴리텍Ⅳ대학 제천캠퍼스",
        "years": "2010",
        "candidate_status": "confirmed_final_graduation_department_and_official_closure",
        "classification": "confirmed_identity_closed_campus_residual_id",
        "kedi_statuses": "폐교|2010-02 폐교",
        "safe_action": "retain_closed_campus_open_id_separately_no_successor_join",
        "summary": (
            "2010년 EDSS에서 입학생·재적학생·교원·직원은 0이고 마지막 졸업생 68명"
            "(여성 3명)만 남아 있다. 메카트로닉스과·산업설비자동화과·자동차과·"
            "정보통신시스템과·컴퓨터응용기계과 등 5개 학과는 대학정보공시에서 모두 "
            "폐과로 표시된다. 국회예산정책처 자료는 한국폴리텍대학 구 제천캠퍼스가 "
            "2010년 2월 폐교했다고 명시하며, 한국폴리텍대학도 제천캠퍼스를 폐교 캠퍼스로 "
            "안내한다. 학적 관리처인 Ⅳ대학 대전캠퍼스와 후속 학교로 자동 병합하지 않는다."
        ),
    },
    "8686164438": {
        "name": "한국폴리텍Ⅶ대학 거창캠퍼스",
        "years": "2010",
        "candidate_status": "confirmed_final_graduation_department_and_official_conversion",
        "classification": "confirmed_identity_closed_campus_conversion_predecessor_id",
        "kedi_statuses": "폐교|2010-03 한국승강기대학 신설",
        "safe_action": "link_site_conversion_open_id_9748064398_keep_entities_separate",
        "summary": (
            "2010년 EDSS에서 입학생·재적학생·교원·직원은 0이고 마지막 졸업생 69명"
            "(여성 14명)만 남아 있다. 산업디자인과·자동차 튜닝과·자동차정비검사과·"
            "전기계측제어과·전자과 등 5개 학과는 대학정보공시에서 모두 폐과로 표시된다. "
            "거창군의회 공식 기록은 한국폴리텍Ⅶ대학 거창캠퍼스 재산의 무상 양도와 같은 "
            "부지에서 2010년 3월 한국승강기대학 개교를 확인한다. 신설 전문대학 OpenID "
            "9748064398은 2010년 입학생 259명·재적학생 256명·졸업생 0명으로 별도 기관이므로 "
            "부지 전환 이력만 연결하고 두 ID는 별도 보존한다."
        ),
    },
    "6924725592": {
        "name": "인천전문대학",
        "years": "2009|2010|2011|2012|2013|2014|2015",
        "candidate_status": "confirmed_longitudinal_metrics_and_official_merger",
        "classification": "confirmed_identity_merger_residual_predecessor_id",
        "kedi_statuses": "기존|폐교|2010-03-01 인천대학교와 통합",
        "safe_action": "link_successor_open_id_3512490093_keep_school_type_metrics_separate",
        "summary": (
            "2009~2015년 EDSS의 인천 남구 공립 전문대학 행이다. 2010~2014년 재적학생·"
            "졸업생과 성별 세부값이 KEDI 인천전문대학 폐교 행과 연도별로 정확히 일치하고, "
            "2015년에는 양쪽 모두 0이다. KEDI가 과거 연도에도 2018년 변경된 미추홀구 명칭을 "
            "적용하고 일부 학과 수가 1개씩 달라 자동 매칭이 실패했다. 인천대학교 공식 연혁의 "
            "2010-03-01 인천전문대학 통합 출범을 근거로 후속 OpenID 3512490093과 이력만 "
            "연결한다. 기존 전문대 잔존 코호트가 2014년까지 별도 집계되므로 두 ID와 학제별 "
            "실적은 분리 보존한다."
        ),
    },
    "3999929010": {
        "name": "구세군사관학교",
        "years": "2009|2010|2011|2012|2013|2014|2015|2016|2017",
        "candidate_status": "confirmed_longitudinal_metrics_and_official_type_conversion",
        "classification": "confirmed_identity_institution_type_conversion_predecessor_id",
        "kedi_statuses": "기존|폐교|2015~ 전문→대학원대학",
        "safe_action": "link_successor_open_id_6883983910_keep_type_specific_metrics_separate",
        "summary": (
            "경기 과천시의 각종학교(전문대학과정)로 신학과 1개를 둔 행이다. 2009~2013년 "
            "입학·재적·졸업·성별·교원 값이 KEDI 구세군사관학교와 연속적으로 일치하고, "
            "2015년 졸업생 14명과 2016년 졸업생 9명도 KEDI 폐교 잔존행과 일치한다. "
            "대학알리미 통폐합 현황은 2015년부터 구세군사관학교→구세군사관대학원대학교, "
            "전문→대학원대학 전환으로 명시한다. 후속 OpenID 6883983910과 전환 이력만 "
            "연결하고, 전환기 중첩 행은 학제별로 분리 보존한다."
        ),
    },
    "6724196135": {
        "name": "인하대학교국제통상물류대학원",
        "years": "2009|2010|2011|2012|2013|2014|2015",
        "candidate_status": "confirmed_exact_longitudinal_metrics_and_closed_status",
        "classification": "confirmed_identity_closed_school_residual_id",
        "kedi_statuses": "폐교|2021 학과상태 폐과",
        "safe_action": "retain_closed_graduate_school_open_id_separately_no_successor_auto_join",
        "summary": (
            "2009~2015년 EDSS의 인천 남구 대학원 행으로, 학과 수·입학·재적·졸업·성별·"
            "교원·직원 값이 KEDI 인하대학교국제통상물류대학원 폐교 행과 모든 연도에 "
            "정확히 일치한다. 2009~2012년에는 잔존 학생·졸업생 실적이 있고 2013~2015년 "
            "핵심 값은 모두 0이다. 2021년 대학정보공시에는 성적분포 9행과 장학금 12행만 "
            "남아 있으며 모든 측정값이 0이고 교통물류학과·국제통상학과는 모두 폐과다. "
            "인하대학교 공식 자료에서 국제통상물류대학원과 물류전문대학원이 동시에 별도 "
            "기관으로 기재되므로 물류전문대학원 OpenID 7490314424와 자동 조인하지 않는다."
        ),
    },
    "7230894923": {
        "name": "백석대학교 문헌정보대학원",
        "years": "2009|2010|2011|2012|2013|2014|2015",
        "candidate_status": "confirmed_unique_department_signature_location_and_official_closure",
        "classification": "confirmed_identity_closed_school_residual_id",
        "kedi_statuses": "폐교|2005-10-01 폐원|학과상태 폐과",
        "safe_action": "retain_closed_graduate_school_open_id_separately_no_successor_auto_join",
        "summary": (
            "2009~2015년 EDSS의 서울 서초구 대학원 행으로, KEDI 백석대학교 문헌정보대학원 "
            "폐교 행과 지역·학교유형·주야간 및 핵심 실적값이 일치한다. 문헌정보교육학전공과 "
            "문헌정보학전공의 정확한 학과명 조합은 이 OpenID에서만 관측되고, 두 학과 모두 "
            "대학정보공시에서 폐과로 표시된다. 실질 학생·졸업·교직원·학과 수는 전 기간 0이며, "
            "2015년 비영 값은 학교수·야간학교수·공학학교수 같은 집계용 표시값뿐이다. 백석대학교 "
            "대학원 학칙 부칙과 공식 연혁은 문헌정보대학원이 2005-10-01 폐원했음을 명시한다. "
            "공식 자료에 직접 지정된 후속 기관은 없으므로 다른 OpenID와 자동 조인하지 않는다."
        ),
    },
    "8145061492": {
        "name": "경기대학교 전통예술대학원",
        "years": "2009|2010|2011|2012|2013|2014|2015",
        "candidate_status": "confirmed_exact_longitudinal_kedi_name_metrics_and_closed_departments",
        "classification": "confirmed_identity_closed_school_residual_id",
        "kedi_statuses": "폐교|학과상태 폐과",
        "safe_action": "retain_closed_graduate_school_open_id_separately_no_successor_auto_join",
        "summary": (
            "2009~2015년 EDSS의 서울 서대문구 야간 대학원 행으로, KEDI에는 같은 기간 매년 "
            "경기대학교 전통예술대학원이 동일 지역·학교유형·주야간·본교 속성의 폐교 행으로 "
            "존재한다. EDSS와 KEDI의 연도별 졸업생 수 8·0·1·1·0·0·0도 정확히 일치한다. "
            "고미술감정·귀금속공예·도자공예·문인화·문화재보존처리·서예 전공은 공시 패널에서 "
            "폐과로 표시된다. 2015년 신규 학생·입학·졸업·교직원 활동은 0이지만 학교수 계열 "
            "집계 표시 6개와 문학 전문학위 누계수여인원 1명이 잔존한다. 미술·디자인대학원 "
            "OpenID 3227136850 및 예술대학원 OpenID 6763784468과 학과 계보상 관련 가능성은 "
            "있으나, 공식적인 OpenID 대응 근거가 없으므로 자동 조인하지 않는다."
        ),
    },
    "9152068436": {
        "name": "백석대학교 기독신학대학원",
        "years": "2009|2010|2011|2012|2013|2014|2015",
        "candidate_status": "confirmed_exact_longitudinal_metrics_and_official_name_change",
        "classification": "confirmed_identity_name_change_predecessor_id",
        "kedi_statuses": "폐교|2009-03-01 신학대학원으로 명칭변경",
        "safe_action": "link_successor_open_id_9246115267_keep_transition_metrics_separate",
        "summary": (
            "2009~2015년 EDSS의 서울 서초구 대학원 행으로, KEDI 백석대학교 기독신학대학원 "
            "폐교 행과 지역·학교유형·주야간 및 연도별 졸업생 수 224·0·0·0·0·0·0이 정확히 "
            "일치한다. 목회학전공은 공시 패널에서 폐과로 표시된다. 백석대학교 공식 대학원 "
            "연혁과 학위수여 규정 부칙은 2009-03-01 기독신학대학원을 신학대학원으로 명칭 "
            "변경했음을 명시한다. 후속 OpenID 9246115267은 같은 2009년에 입학생 238명과 "
            "재적학생 927명을 담고, 구 ID는 졸업생 224명을 담으므로 전환기 실적은 분리 "
            "보존한다. 2015년의 비영 값 6개는 모두 학교수 계열 집계 표시다."
        ),
    },
}


def closed_identity_override(
    name: str,
    years: str,
    activity: str,
    department_note: str,
    kedi_statuses: str = "폐교",
) -> dict[str, str]:
    """Build a conservative confirmation for a KEDI-matched closed-school residual."""

    return {
        "name": name,
        "years": years,
        "candidate_status": "confirmed_kedi_metric_location_and_department_context",
        "classification": "confirmed_identity_closed_school_residual_id",
        "kedi_statuses": kedi_statuses,
        "safe_action": "retain_closed_graduate_school_open_id_separately_no_successor_auto_join",
        "summary": (
            f"EDSS에서 {activity}. 같은 연도·지역·학교유형의 KEDI {name} 행과 핵심 수치가 "
            f"일치한다. {department_note} 따라서 학교명은 확정하되 폐교 잔존 OpenID로 별도 "
            "보존하고 다른 대학원과 자동 조인하지 않는다."
        ),
    }


# The remaining nonzero named candidates were reviewed as one batch.  Each row
# below has a unique same-year KEDI residual match and corroborating EDSS
# department/location context.  Zero-only or unnamed candidates are deliberately
# not promoted by this batch.
CONFIRMED_IDENTITY_OVERRIDES.update(
    {
        "2012388527": closed_identity_override(
            "경남대학교 북한대학원",
            "2016|2017|2018|2019",
            "졸업생 수가 2016~2019년 1·3·0·0명으로 KEDI와 일치한다",
            "경제·군사안보·정치통일 등 7개 학과는 후속 패널에서 모두 폐과다.",
            "기존|폐교",
        ),
        "7059025943": closed_identity_override(
            "서울여자대학교사회복지대학원", "2009|2010|2011",
            "2010년 졸업생 4명만 비영인 잔존 실적이 KEDI와 일치한다",
            "노인복지전공과 사회복지전공은 폐과다.",
        ),
        "8253922164": closed_identity_override(
            "동명정보대학교 건축대학원", "2009|2010|2011",
            "졸업생 수가 2009~2011년 5·1·0명으로 KEDI와 일치한다",
            "건축공학과와 건축학과가 확인된다.",
        ),
        "8523545861": closed_identity_override(
            "고신대학교생활정보대학원", "2009|2010|2011",
            "2010년 졸업생 1명만 비영인 잔존 실적이 KEDI와 일치한다",
            "시각영상디자인전공은 폐과다.",
        ),
        "9298671543": closed_identity_override(
            "서경대학교경영행정대학원", "2009|2010|2011",
            "졸업생 수가 2009~2011년 1·3·0명으로 KEDI와 일치한다",
            "미용경영학과가 확인된다.",
        ),
        "4089279294": closed_identity_override(
            "선문대학교 산업정보통신대학원", "2009|2010",
            "2010년 졸업생 2명이 KEDI 폐교 행과 일치한다",
            "금속재료공학과 등 5개 학과에서 신설·폐과 상태가 함께 관측된다.",
        ),
        "6188584208": closed_identity_override(
            "서경대학교사회과학대학원", "2009|2010",
            "졸업생 수가 2009~2010년 3·1명으로 KEDI와 일치한다",
            "청소년학과는 폐과다.",
        ),
        "1400377094": closed_identity_override(
            "상명대학교 디지털미디어대학원", "2009",
            "2009년 졸업생 16명이 KEDI 폐교 행과 일치한다",
            "게임·디지털영상·디지털콘텐츠 등 6개 학과는 폐과다.",
        ),
        "2838729381": closed_identity_override(
            "영산대학교(산업대) 부동산대학원", "2009",
            "2009년 졸업생 3명이 KEDI 폐교 행과 일치한다",
            "부동산전공은 폐과다.",
        ),
        "3083711624": closed_identity_override(
            "영산대학교 조리대학원", "2009",
            "2009년 졸업생 1명이 KEDI 폐교 행과 일치한다",
            "조리학전공은 폐과다.",
        ),
        "3987692773": closed_identity_override(
            "인제대학교 첨단산업기술대학원", "2009",
            "2009년 졸업생 2명이 KEDI 폐교 행과 일치한다",
            "기계공학전공은 폐과다.",
        ),
        "4197857260": closed_identity_override(
            "한성대학교안전보건경영대학원", "2009",
            "2009년 졸업생 1명이 KEDI 폐교 행과 일치한다",
            "산업보건공학과 등 3개 학과는 폐과다.",
        ),
        "5183901667": closed_identity_override(
            "서울장신대학교 목회상담대학원", "2009",
            "2009년 졸업생 1명이 KEDI 폐교 행과 일치한다",
            "목회상담학과는 폐과다.",
        ),
        "6363005578": closed_identity_override(
            "서경대학교물류대학원", "2009",
            "2009년 졸업생 5명이 KEDI 폐교 행과 일치한다",
            "물류학과는 폐과다.",
        ),
        "6722269237": closed_identity_override(
            "명지대학교 투자정보대학원", "2011",
            "2011년 졸업생 1명이 KEDI 폐교 행과 일치한다",
            "국제경영학과가 같은 OpenID에서 확인된다.",
        ),
        "6891424894": closed_identity_override(
            "한국교원대학교교육정책대학원", "2009",
            "2009년 졸업생 45명이 KEDI 폐교 행과 일치한다",
            "교육정책학과는 폐과다.",
        ),
        "7130860865": closed_identity_override(
            "숭실대학교노사관계대학원", "2009",
            "2009년 졸업생 9명이 KEDI 폐교 행과 일치한다",
            "노동법·노사관계·노사조정·인력경영 학과는 모두 폐과다.",
        ),
        "8713748276": closed_identity_override(
            "남부대학교 경찰행정대학원", "2009",
            "2009년 졸업생 1명이 KEDI 폐교 행과 일치한다",
            "국방정책학과는 폐과다.",
        ),
    }
)

CONFIRMED_IDENTITY_OVERRIDES.update(
    {
        "4708137869": {
            "name": "우송대학교 공학대학원",
            "years": "2009|2012|2013|2014|2015|2016",
            "candidate_status": "confirmed_kedi_metric_department_and_official_name_lineage",
            "classification": "confirmed_identity_closed_name_change_predecessor_id",
            "kedi_statuses": "폐교|2006-11-03 공학·디자인대학원으로 통합 명칭변경",
            "safe_action": "retain_predecessor_open_id_separately_no_unique_successor_auto_join",
            "summary": (
                "2009년 EDSS와 KEDI 우송대학교 공학대학원 폐교 행의 졸업생 7명이 일치하고, "
                "2012~2016년 핵심 활동값은 0이다. 건축공학과·전자정보통신공학과·"
                "철도건설환경공학과·컴퓨터과학과가 확인되며 일부는 폐과다. 공식 연혁은 "
                "2006-11-03 공학대학원과 디자인대학원을 공학·디자인대학원으로 변경했다고 "
                "명시한다. 후속 명칭의 EDSS ID가 둘이므로 특정 ID와 자동 조인하지 않는다."
            ),
        },
        "1789836525": {
            "name": "한국정보통신대학교 경영전문대학원",
            "years": "2009",
            "candidate_status": "confirmed_kedi_metric_and_official_kaist_merger",
            "classification": "confirmed_identity_merger_residual_predecessor_id",
            "kedi_statuses": "폐교|2009-03-01 KAIST 통합",
            "safe_action": "link_kaist_merger_history_keep_predecessor_open_id_separate_no_metric_join",
            "summary": (
                "2009년 EDSS의 졸업생 11명과 경영전공 폐과 기록이 같은 지역 KEDI "
                "한국정보통신대학교 경영전문대학원 폐교 행과 일치한다. KAIST 공식 연혁은 "
                "2009-03-01 한국정보통신학원(ICU·한국정보통신대학교) 통합을 확인한다. "
                "통합 이력만 연결하고 이 OpenID의 실적은 별도 보존한다."
            ),
        },
        "8625397384": {
            "name": "한국정보통신대학교 정보통신대학원",
            "years": "2009",
            "candidate_status": "confirmed_kedi_metric_and_official_kaist_merger",
            "classification": "confirmed_identity_merger_residual_predecessor_id",
            "kedi_statuses": "폐교|2009-03-01 KAIST 통합",
            "safe_action": "link_kaist_merger_history_keep_predecessor_open_id_separate_no_metric_join",
            "summary": (
                "2009년 EDSS의 졸업생 145명과 IT경영학부·공학부 폐과 기록이 같은 지역 "
                "KEDI 한국정보통신대학교 정보통신대학원 폐교 행과 일치한다. KAIST 공식 "
                "연혁은 2009-03-01 ICU 통합을 확인한다. 통합 이력만 연결하고 실적은 별도 보존한다."
            ),
        },
        "9771132469": {
            "name": "한국정보통신대학교",
            "years": "2009",
            "candidate_status": "confirmed_kedi_metric_and_official_kaist_merger",
            "classification": "confirmed_identity_merger_residual_predecessor_id",
            "kedi_statuses": "폐교|2009-03-01 KAIST 통합",
            "safe_action": "link_kaist_merger_history_keep_predecessor_open_id_separate_no_metric_join",
            "summary": (
                "2009년 EDSS의 졸업생 34명과 IT경영학부·공학부 폐과 기록이 같은 지역 KEDI "
                "한국정보통신대학교 폐교 행과 일치한다. KAIST 공식 연혁은 2009-03-01 "
                "한국정보통신대학교 통합을 확인한다. 통합 이력만 연결하고 학교 실적은 별도 보존한다."
            ),
        },
        "7238059103": {
            "name": "국민대학교산업기술대학원",
            "years": "2009",
            "candidate_status": "confirmed_kedi_metric_department_and_official_name_change",
            "classification": "confirmed_identity_closed_name_change_predecessor_id",
            "kedi_statuses": "폐교|2008-10 공학대학원으로 명칭변경",
            "safe_action": "link_successor_open_id_9552361845_keep_transition_metrics_separate",
            "summary": (
                "2009년 EDSS 졸업생 수가 같은 지역 KEDI 국민대학교산업기술대학원 폐교 행과 "
                "일치하고, 8개 학과 집합은 후속 공학대학원 OpenID 9552361845와 정확히 같다. "
                "국민대학교 공식 연혁은 2008-10 산업기술대학원을 공학대학원으로 명칭변경했다고 "
                "명시한다. 명칭변경 이력만 연결하고 전환기 실적은 두 ID에 분리 보존한다."
            ),
        },
        "8336962282": {
            "name": "수원대학교음악테크놀로지대학원",
            "years": "2009",
            "candidate_status": "confirmed_kedi_metric_and_exact_department_signature",
            "classification": "confirmed_identity_closed_school_residual_id",
            "kedi_statuses": "폐교|음악대학원 동일 학과서명",
            "safe_action": "retain_closed_open_id_separately_no_successor_auto_join",
            "summary": (
                "2009년 EDSS 졸업생 수가 같은 지역 KEDI 수원대학교음악테크놀로지대학원 "
                "폐교 행과 일치한다. 뮤직테크놀로지학과·피아노교수학과 조합은 음악대학원 "
                "OpenID 3225332260과 정확히 같지만, 공식 자료에서 두 대학원의 직접 OpenID "
                "승계는 확인되지 않는다. 학교명은 확정하되 두 ID를 자동 조인하지 않는다."
            ),
        },
        "9152388691": {
            "name": "서울과학기술대학교(산업대학) 주택도시대학원",
            "years": "2017|2018",
            "candidate_status": "confirmed_kedi_residual_and_exact_department_signature",
            "classification": "confirmed_identity_closed_school_residual_id",
            "kedi_statuses": "폐교|현재 주택도시대학원 동일 학과서명",
            "safe_action": "retain_historical_parent_type_open_id_separately_no_auto_join",
            "summary": (
                "2018년 같은 지역 KEDI의 서울과학기술대학교(산업대학) 주택도시대학원 폐교 "
                "잔존 행과 대응하고, 5개 학과 집합은 확인 ID 8655836164의 주택도시대학원과 "
                "정확히 같다. 공식 홈페이지는 주택도시대학원이 2001년 설립되어 현재도 운영됨을 "
                "확인한다. 역사적 모체 유형 잔존 ID로 보존하고 확인 ID와 자동 병합하지 않는다."
            ),
        },
        "4264510691": {
            "name": "가천의과학대학교병원경영대학원",
            "years": "2012",
            "candidate_status": "confirmed_unique_exact_department_signature_and_location",
            "classification": "confirmed_identity_closed_school_residual_id",
            "kedi_statuses": "확인 ID 3341381788 동일 학과서명|폐과",
            "safe_action": "retain_duplicate_residual_open_id_separately_no_auto_join",
            "summary": (
                "2012년 인천 연수구에서 병원경영학전공·의료정보학전공의 정확한 조합이 이름이 "
                "확인된 OpenID 3341381788과 일치하고, 후속 패널에서 두 학과는 폐과다. 동일 기관의 "
                "잔존 중복 ID로 신원은 확정하되 공식 키 승계표가 없으므로 자동 병합하지 않는다."
            ),
        },
        "9057022750": {
            "name": "가천의과학대학교영상정보대학원",
            "years": "2012",
            "candidate_status": "confirmed_unique_exact_department_signature_and_location",
            "classification": "confirmed_identity_closed_school_residual_id",
            "kedi_statuses": "확인 ID 5739951491 동일 학과서명|폐과",
            "safe_action": "retain_duplicate_residual_open_id_separately_no_auto_join",
            "summary": (
                "2012년 인천 연수구에서 디지털영상그래픽스·디지털영상커뮤니케이션·"
                "디지털정보공학전공의 정확한 조합이 이름이 확인된 OpenID 5739951491과 일치하고, "
                "후속 패널에서 세 학과는 폐과다. 동일 기관의 잔존 중복 ID로 신원은 확정하되 "
                "공식 키 승계표가 없으므로 자동 병합하지 않는다."
            ),
        },
        "9130622029": {
            "name": "대불대학교산업기술대학원|세한대학교산업기술대학원",
            "years": "2009|2010|2011|2012|2013|2014|2015|2016|2017|2018|2019|2020|2021|2022",
            "candidate_status": "confirmed_location_closed_status_departments_and_official_name_history",
            "classification": "confirmed_identity_closed_school_residual_id",
            "kedi_statuses": "폐교|2012 대불대학교→세한대학교 교명변경",
            "safe_action": "retain_closed_graduate_school_open_id_separately_no_successor_auto_join",
            "summary": (
                "2009~2022년 EDSS의 전남 영암군 야간 대학원 행이다. 0101의 입학·재적·졸업·"
                "교직원·학과 수는 전 기간 0이고, 2022년 비영 값 6개는 학교수 계열 집계 "
                "표시뿐이다. 산업디자인학과·레저스포츠산업학과·건축토목도시공학전공은 "
                "대학정보공시에서 모두 폐과다. 같은 위치 KEDI에는 2009~2012년 대불대학교"
                "산업기술대학원, 2013년 이후 세한대학교산업기술대학원이 야간 특수대학원 "
                "폐교 행으로 존재한다. 세한대학교 공식 연혁은 1997년 산업기술대학원 개원과 "
                "2012년 대불대학교→세한대학교 교명변경을 확인한다. 학교명은 확정하되 다른 "
                "대학원 OpenID와 자동 조인하지 않는다."
            ),
        },
    }
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def qid(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def expand_year_window(value: str) -> str:
    """Convert a reviewed inclusive year window to the override year format."""

    value = value.strip()
    if re.fullmatch(r"\d{4}", value):
        return value
    match = re.fullmatch(r"(\d{4})-(\d{4})", value)
    if not match:
        raise RuntimeError(f"invalid evidence_window: {value!r}")
    first_year, last_year = (int(part) for part in match.groups())
    if first_year > last_year:
        raise RuntimeError(f"reversed evidence_window: {value!r}")
    return "|".join(str(year) for year in range(first_year, last_year + 1))


def load_approved_identity_proposals(path: Path) -> dict[str, dict[str, str]]:
    """Load user-approved identity decisions without creating any OpenID merge."""

    required = {
        "open_id",
        "proposed_entity_name",
        "decision_bucket",
        "proposed_manual_classification",
        "evidence_window",
        "kedi_match_evidence",
        "safe_join_action",
        "notes",
    }
    rows = read_csv(path)
    if not rows:
        raise RuntimeError(f"approved identity proposal file is empty: {path}")
    missing_fields = required - set(rows[0])
    if missing_fields:
        raise RuntimeError(f"approved identity proposal fields missing: {sorted(missing_fields)}")

    overrides: dict[str, dict[str, str]] = {}
    for row in rows:
        open_id = row["open_id"].strip()
        if not re.fullmatch(r"\d+", open_id):
            raise RuntimeError(f"invalid approved OpenID: {open_id!r}")
        if open_id in overrides:
            raise RuntimeError(f"duplicate approved OpenID: {open_id}")
        if row["decision_bucket"].strip() != "ready_to_record":
            raise RuntimeError(f"proposal is not approved for recording: {open_id}")
        safe_action = row["safe_join_action"].strip()
        if "separate" not in safe_action and "no_auto_join" not in safe_action:
            raise RuntimeError(f"unsafe identity action for {open_id}: {safe_action!r}")
        evidence = row["kedi_match_evidence"].strip()
        note = row["notes"].strip()
        overrides[open_id] = {
            "name": row["proposed_entity_name"].strip(),
            "years": expand_year_window(row["evidence_window"]),
            "candidate_status": "confirmed_manual_reviewed_kedi_identity",
            "classification": row["proposed_manual_classification"].strip(),
            "kedi_statuses": "수동검토확정|KEDI·학과·공식이력 교차검증",
            "safe_action": safe_action,
            "summary": (
                f"수동 전수검토에서 {row['proposed_entity_name'].strip()}으로 확정했다. "
                f"{evidence}. "
                + (f"{note}. " if note else "")
                + "학교명만 기록하며 역사 OpenID는 별도 보존하고 자동 병합하지 않는다."
            ),
        }
    return overrides


def load_final_identity_decisions(path: Path) -> dict[str, dict[str, str]]:
    """Load the final reviewed names while keeping every historical OpenID separate."""

    required = {
        "open_id",
        "confirmed_entity_name",
        "decision_bucket",
        "manual_classification",
        "evidence_window",
        "evidence_tier",
        "kedi_statuses",
        "official_source_url",
        "safe_join_action",
        "evidence_summary",
    }
    rows = read_csv(path)
    if not rows:
        raise RuntimeError(f"final identity decision file is empty: {path}")
    missing_fields = required - set(rows[0])
    if missing_fields:
        raise RuntimeError(f"final identity decision fields missing: {sorted(missing_fields)}")

    overrides: dict[str, dict[str, str]] = {}
    for row in rows:
        open_id = row["open_id"].strip()
        if not re.fullmatch(r"\d+", open_id):
            raise RuntimeError(f"invalid final-review OpenID: {open_id!r}")
        if open_id in overrides:
            raise RuntimeError(f"duplicate final-review OpenID: {open_id}")
        if row["decision_bucket"].strip() != "ready_to_record":
            raise RuntimeError(f"final decision is not approved for recording: {open_id}")
        classification = row["manual_classification"].strip()
        if not classification.startswith("confirmed_identity_"):
            raise RuntimeError(f"unconfirmed final classification for {open_id}: {classification!r}")
        safe_action = row["safe_join_action"].strip()
        if "separate" not in safe_action and "no_auto_join" not in safe_action:
            raise RuntimeError(f"unsafe final identity action for {open_id}: {safe_action!r}")
        if not row["evidence_tier"].strip() or not row["evidence_summary"].strip():
            raise RuntimeError(f"final decision lacks evidence for {open_id}")
        overrides[open_id] = {
            "name": row["confirmed_entity_name"].strip(),
            "years": expand_year_window(row["evidence_window"]),
            "candidate_status": "confirmed_final_manual_review_identity",
            "classification": classification,
            "kedi_statuses": row["kedi_statuses"].strip(),
            "safe_action": safe_action,
            "summary": (
                f"최종 수동검토에서 {row['confirmed_entity_name'].strip()}으로 확정했다. "
                f"{row['evidence_summary'].strip()} 학교명만 기록하며 역사 OpenID는 별도 "
                "보존하고 자동 병합하지 않는다."
            ),
        }
    return overrides


def compact(values: set[str], limit: int = 8) -> str:
    cleaned = sorted({value.strip() for value in values if value and value.strip()})
    return "|".join(cleaned[:limit])


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def numeric_expression(columns: list[str]) -> str:
    eligible = [
        column
        for column in columns
        if not column.startswith("_")
        and column not in {"조사년도", "개방ID", "적용년도"}
        and "식별" not in column
        and "코드" not in column
        and "년월" not in column
        and "연도" not in column
    ]
    terms = [
        f"case when try_cast(trim({qid(column)}) as double) <> 0 then 1 else 0 end"
        for column in eligible
    ]
    return " + ".join(terms) if terms else "0"


def collect_panel_evidence(
    connection: duckdb.DuckDBPyConnection, target_ids: set[str]
) -> tuple[dict[tuple[str, str, str], dict], dict[str, set[str]], dict[str, set[str]]]:
    placeholders = ",".join("?" for _ in target_ids)
    params = sorted(target_ids)
    tables = connection.execute(
        """
        select distinct table_schema, table_name
        from information_schema.columns
        where column_name = '개방ID'
          and table_schema in ('higher_education', 'university_disclosure')
        order by 1, 2
        """
    ).fetchall()
    evidence: dict[tuple[str, str, str], dict] = {}
    all_departments: dict[str, set[str]] = defaultdict(set)
    all_statuses: dict[str, set[str]] = defaultdict(set)

    for schema, table in tables:
        full_name = f"{qid(schema)}.{qid(table)}"
        columns = [row[0] for row in connection.execute(f"describe {full_name}").fetchall()]
        year_column = "_panel_year" if "_panel_year" in columns else "조사년도"
        department_sql = (
            ", string_agg(distinct nullif(trim(학과명), ''), ' | ') as departments"
            if "학과명" in columns
            else ", '' as departments"
        )
        status_sql = (
            ", string_agg(distinct nullif(trim(학과상태명), ''), ' | ') as statuses"
            if "학과상태명" in columns
            else ", '' as statuses"
        )
        sql = f"""
            select trim(개방ID) as open_id, trim({qid(year_column)}) as panel_year,
                   count(*) as row_count,
                   sum({numeric_expression(columns)}) as nonzero_measure_count
                   {department_sql}{status_sql}
            from {full_name}
            where trim(개방ID) in ({placeholders})
            group by 1, 2
        """
        for open_id, year, row_count, nonzero_count, departments, statuses in connection.execute(
            sql, params
        ).fetchall():
            if not open_id or not year:
                continue
            key = (open_id, str(year), f"{schema}.{table}")
            dept_set = {value.strip() for value in (departments or "").split(" | ") if value.strip()}
            status_set = {value.strip() for value in (statuses or "").split(" | ") if value.strip()}
            evidence[key] = {
                "rows": int(row_count),
                "nonzero": int(nonzero_count or 0),
                "departments": dept_set,
                "statuses": status_set,
            }
            all_departments[open_id].update(dept_set)
            all_statuses[open_id].update(status_set)
    return evidence, all_departments, all_statuses


def candidate_evidence(
    annual_rows: list[dict[str, str]], match_rows: list[dict[str, str]], target_ids: set[str]
) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in annual_rows:
        if row["openid"] in target_ids and row.get("candidate_school_name", "").strip():
            result[row["openid"]].append(
                {
                    "year": row["year"],
                    "name": row["candidate_school_name"].strip(),
                    "status": "",
                }
            )
    status_lookup: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in match_rows:
        if row["openid"] in target_ids and row.get("match_confidence") == "candidate":
            status_lookup[(row["openid"], row["year"], row["kedi_school_name"].strip())].add(
                row.get("kedi_school_status", "").strip()
            )
    for open_id, rows in result.items():
        for row in rows:
            row["status"] = compact(status_lookup[(open_id, row["year"], row["name"])])
    return result


def department_signature_candidates(
    connection: duckdb.DuckDBPyConnection,
    target_ids: set[str],
    identity_names: dict[str, str],
    annual_rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Find conservative predecessor/successor clues from exact department sets.

    A match needs at least two departments, an exact same-table/same-year set,
    a named comparison OpenID, and overlapping 0101 region context.
    """
    regions: dict[str, set[str]] = defaultdict(set)
    for row in annual_rows:
        if row.get("regions", "").strip():
            regions[row["openid"]].update(
                value.strip() for value in row["regions"].split(" | ") if value.strip()
            )
    signatures: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for table in (
        "higher_education.panel_0231",
        "university_disclosure.panel_0715",
        "university_disclosure.panel_1017",
    ):
        for open_id, year, department in connection.execute(
            f"select trim(개방ID), trim(_panel_year), trim(학과명) from {table} "
            "where coalesce(trim(개방ID), '') <> '' and coalesce(trim(학과명), '') <> ''"
        ).fetchall():
            signatures[(table, str(year), open_id)].add(normalize_text(department))

    inverse: dict[tuple[str, str, tuple[str, ...]], list[str]] = defaultdict(list)
    for (table, year, open_id), departments in signatures.items():
        if len(departments) >= 2:
            inverse[(table, year, tuple(sorted(departments)))].append(open_id)

    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for (table, year, open_id), departments in signatures.items():
        if open_id not in target_ids or len(departments) < 2:
            continue
        comparison_ids = [
            other
            for other in inverse[(table, year, tuple(sorted(departments)))]
            if other != open_id
            and identity_names.get(other, "")
            and regions.get(open_id, set()) & regions.get(other, set())
        ]
        names = sorted({identity_names[other] for other in comparison_ids})
        if len(names) != 1:
            continue
        key = (open_id, year, names[0])
        if key in seen:
            continue
        seen.add(key)
        result[open_id].append(
            {"year": year, "name": names[0], "status": "exact_department_signature_successor"}
        )
    return result


def classify(
    school_type: str,
    all_statuses: set[str],
    candidates: list[dict[str, str]],
    latest_nonzero: int,
) -> str:
    candidate_statuses = {item["status"] for item in candidates if item["status"]}
    if any("폐과" in status for status in all_statuses) or any(
        "폐교" in status for status in candidate_statuses
    ):
        return "closed_school_or_department_residual_id"
    if school_type not in {"대학원", "대학원대학"}:
        return "historical_out_of_scope_or_inactive_identity_id"
    if candidates:
        return "historical_reorganization_or_successor_candidate_id"
    if latest_nonzero == 0:
        return "unresolved_historical_zero_activity_id"
    return "unresolved_historical_identity_id"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--duckdb", type=Path, default=DEFAULT_DUCKDB)
    parser.add_argument("--approved-identity-proposals", type=Path)
    parser.add_argument("--final-identity-decisions", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    identity_path = root / "data/processed/edss_0101_kedi_openid_identity_2009_2025.csv"
    annual_path = root / "data/processed/edss_0101_kedi_crosswalk_2009_2025.csv"
    match_path = root / "data/processed/edss_0101_kedi_row_match_evidence_2009_2025.csv"
    reviewed_2025_path = root / "data/metadata/edss_2025_unmatched_openid_manual_review.csv"
    approved_proposals_path = (
        args.approved_identity_proposals.resolve()
        if args.approved_identity_proposals
        else root / "data/metadata/edss_remaining_unnamed_openid_identity_proposals.csv"
    )
    final_decisions_path = (
        args.final_identity_decisions.resolve()
        if args.final_identity_decisions
        else root / "data/metadata/edss_remaining_pre2025_openid_identity_decisions.csv"
    )
    output_path = root / "data/metadata/edss_pre2025_unmatched_openid_manual_review.csv"
    summary_path = root / "data/metadata/edss_pre2025_unmatched_openid_manual_review.json"

    identities = read_csv(identity_path)
    reviewed_2025 = {row["open_id"] for row in read_csv(reviewed_2025_path)}
    targets = [
        row
        for row in identities
        if row["identity_status"] == "unmatched" and row["openid"] not in reviewed_2025
    ]
    targets.sort(key=lambda row: (-int(row["edss_year_count"]), row["openid"]))
    target_ids = {row["openid"] for row in targets}
    confirmed_identity_overrides = dict(CONFIRMED_IDENTITY_OVERRIDES)
    approved_overrides = load_approved_identity_proposals(approved_proposals_path)
    unexpected_approved_ids = set(approved_overrides) - target_ids
    if unexpected_approved_ids:
        raise RuntimeError(
            f"approved identities are outside the review population: {sorted(unexpected_approved_ids)}"
        )
    confirmed_identity_overrides.update(approved_overrides)
    final_overrides = load_final_identity_decisions(final_decisions_path)
    if set(final_overrides) != FINAL_REVIEW_IDS:
        raise RuntimeError(
            "final identity decision population mismatch: "
            f"missing={sorted(FINAL_REVIEW_IDS - set(final_overrides))}, "
            f"extra={sorted(set(final_overrides) - FINAL_REVIEW_IDS)}"
        )
    unexpected_final_ids = set(final_overrides) - target_ids
    if unexpected_final_ids:
        raise RuntimeError(
            f"final identities are outside the review population: {sorted(unexpected_final_ids)}"
        )
    overlap = set(approved_overrides) & set(final_overrides)
    if overlap:
        raise RuntimeError(f"duplicate identities across decision inputs: {sorted(overlap)}")
    confirmed_identity_overrides.update(final_overrides)
    annual_rows = read_csv(annual_path)
    match_rows = read_csv(match_path)
    annual_lookup = {
        (row["openid"], row["year"]): row for row in annual_rows if row["openid"] in target_ids
    }

    connection = duckdb.connect(str(args.duckdb), read_only=True)
    panel, all_departments, all_statuses = collect_panel_evidence(connection, target_ids)
    identity_names = {
        row["openid"]: row["latest_direct_school_name"]
        for row in identities
        if row["latest_direct_school_name"].strip()
    }
    signature_candidates = department_signature_candidates(
        connection, target_ids, identity_names, annual_rows
    )
    connection.close()
    candidates = candidate_evidence(annual_rows, match_rows, target_ids)
    for open_id, rows in signature_candidates.items():
        candidates[open_id].extend(rows)

    output_rows: list[dict[str, object]] = []
    for order, identity in enumerate(targets, start=1):
        open_id = identity["openid"]
        years = sorted({int(year) for oid, year, _table in panel if oid == open_id})
        latest_any_year = str(max(years)) if years else identity["last_edss_year"]
        latest_items = [
            value for (oid, year, _table), value in panel.items() if oid == open_id and year == latest_any_year
        ]
        latest_tables = {
            table for (oid, year, table) in panel if oid == open_id and year == latest_any_year
        }
        latest_statuses: set[str] = set()
        latest_departments: set[str] = set()
        for item in latest_items:
            latest_statuses.update(item["statuses"])
            latest_departments.update(item["departments"])
        latest_rows = sum(item["rows"] for item in latest_items)
        latest_nonzero = sum(item["nonzero"] for item in latest_items)
        candidate_rows = candidates.get(open_id, [])
        override = CONTEXT_OVERRIDES.get(open_id)
        confirmed_override = confirmed_identity_overrides.get(open_id)
        if confirmed_override:
            candidate_rows = [
                {
                    "year": year,
                    "name": confirmed_override["name"],
                    "status": confirmed_override["kedi_statuses"],
                }
                for year in confirmed_override["years"].split("|")
            ]
        if override and not candidate_rows:
            override_name, override_years, override_status = override
            candidate_rows = [
                {"year": year, "name": override_name, "status": override_status}
                for year in override_years.split("|")
            ]
        candidate_counts = Counter(row["name"] for row in candidate_rows)
        likely_candidate = (
            sorted(candidate_counts, key=lambda name: (-candidate_counts[name], name))[0]
            if candidate_counts
            else ""
        )
        evidence_years = sorted({row["year"] for row in candidate_rows if row["name"] == likely_candidate})
        kedi_statuses = {
            row["status"] for row in candidate_rows if row["name"] == likely_candidate and row["status"]
        }
        annual_last = annual_lookup[(open_id, identity["last_edss_year"])]
        classification = classify(
            annual_last.get("edss_school_type", ""),
            all_statuses.get(open_id, set()),
            candidate_rows,
            latest_nonzero,
        )
        if override:
            if "closed" in override[2]:
                classification = "closed_school_or_department_residual_id"
            else:
                classification = "historical_reorganization_or_successor_candidate_id"
        if confirmed_override:
            classification = confirmed_override["classification"]
        if likely_candidate:
            if confirmed_override:
                candidate_status = confirmed_override["candidate_status"]
            elif "exact_department_signature_successor" in kedi_statuses:
                candidate_status = "probable_exact_department_signature_successor"
            elif override:
                candidate_status = "probable_same_year_context_candidate"
            else:
                candidate_status = (
                    "probable_repeated_candidate" if len(evidence_years) >= 2 else "unconfirmed_candidate"
                )
        else:
            candidate_status = "no_reliable_name_candidate"
        if classification == "closed_school_or_department_residual_id":
            safe_action = "retain_open_id_exclude_from_active_entity_analysis_no_auto_join"
        elif classification == "historical_reorganization_or_successor_candidate_id":
            safe_action = "retain_predecessor_open_id_separately_no_auto_join"
        else:
            safe_action = "retain_open_id_exclude_from_active_entity_analysis_pending_evidence"
        if confirmed_override:
            safe_action = confirmed_override["safe_action"]

        status_text = compact(latest_statuses) or "표지 없음"
        candidate_text = likely_candidate or "신뢰할 학교명 후보 없음"
        summary = (
            f"0101에는 {identity['first_edss_year']}~{identity['last_edss_year']}년 "
            f"{identity['edss_year_count']}개 연도가 있고, 전 패널 최종 관측은 {latest_any_year}년 "
            f"{len(latest_tables)}개 패널 {latest_rows}행이다. 최종 관측의 비영 측정 셀은 "
            f"{latest_nonzero}개, 학과상태는 {status_text}이다. 동년 KEDI 수치 후보는 "
            f"{candidate_text}이며 직접 학교명 연결이 아니므로 자동 조인하지 않는다."
        )
        if confirmed_override:
            summary = confirmed_override["summary"]
        output_rows.append(
            {
                "review_order": order,
                "open_id": open_id,
                "first_year": identity["first_edss_year"],
                "last_0101_year": identity["last_edss_year"],
                "year_count": identity["edss_year_count"],
                "latest_any_panel_year": latest_any_year,
                "identity_status": identity["identity_status"],
                "manual_classification": classification,
                "likely_entity_candidate": likely_candidate,
                "candidate_status": candidate_status,
                "candidate_evidence_years": "|".join(evidence_years),
                "candidate_kedi_statuses": compact(kedi_statuses),
                "latest_panel_count": len(latest_tables),
                "latest_row_count": latest_rows,
                "latest_nonzero_measure_count": latest_nonzero,
                "latest_department_status": status_text,
                "department_examples": compact(latest_departments or all_departments.get(open_id, set())),
                "safe_join_action": safe_action,
                "evidence_summary": summary,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output_rows)

    output_ids = [str(row["open_id"]) for row in output_rows]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "all unmatched EDSS 0101 OpenIDs not already reviewed in the 2025-ending batch",
        "row_count": len(output_rows),
        "unique_open_id_count": len(set(output_ids)),
        "missing_target_ids": sorted(target_ids - set(output_ids)),
        "extra_output_ids": sorted(set(output_ids) - target_ids),
        "duplicate_open_ids": sorted(
            {open_id for open_id, count in Counter(output_ids).items() if count > 1}
        ),
        "classification_counts": dict(
            sorted(Counter(str(row["manual_classification"]) for row in output_rows).items())
        ),
        "candidate_status_counts": dict(
            sorted(Counter(str(row["candidate_status"]) for row in output_rows).items())
        ),
        "source_checksums": {
            str(identity_path.relative_to(root)): sha256(identity_path),
            str(annual_path.relative_to(root)): sha256(annual_path),
            str(match_path.relative_to(root)): sha256(match_path),
            str(reviewed_2025_path.relative_to(root)): sha256(reviewed_2025_path),
            str(approved_proposals_path.relative_to(root)): sha256(approved_proposals_path),
            str(final_decisions_path.relative_to(root)): sha256(final_decisions_path),
            str(args.duckdb): sha256(args.duckdb),
        },
        "output": {
            "path": str(output_path.relative_to(root)),
            "sha256": sha256(output_path),
        },
        "caveats": [
            "A KEDI candidate is supporting evidence, not an automatic identity join.",
            "Zero, missing, not-applicable, and disclosure restrictions remain distinct in source data.",
            "OpenIDs are retained as strings and historical predecessor IDs remain separate.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
