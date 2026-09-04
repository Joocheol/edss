# EDSS 남은 식별자 공백 처리

기준 실행일: 2026-09-04

## 결론

취업통계 2023–2024년은 2010–2022년의 146열 개인형 자료에서 24열 학교·학과 집계형 자료로 원천 형식과 분석 grain이 바뀌었고 `개방ID`도 사라졌다. 공식 학교명–OpenID 교차표가 없으므로 **46,962행·3,281개 학교연도 전체를 기존 OpenID 종단 패널의 통합 대상에서 제외**한다. 원본과 개인정보 없는 파생 자료는 삭제하지 않고 2023–2024년 독립 참고 자료로 보존한다.

대학알리미 후보를 적용한 별도 파생 파일의 13,920행과 미연결 33,042행은 이 결정을 검산하는 과거 산출물로 유지한다. 적용 ID도 공식 교차표가 아닌 추론값이므로 46,962행 모두 기존 종단 패널 적격 행으로 승격하지 않는다. 정식 ID 대입·인접 연도 보간·원본 수정은 0건이다.

미연결률 1% 초과 패널 4개도 모두 재검토했다. `0202`, `0204`, `1102`는 미연결 키가 모두 각 ID의 `0101` 관측기간 밖에 있어 기준기간 차이로 설명된다. `1209 법인임원현황`의 2019년 OpenID `5831784427` 내부 공백 1개·16행은 한국폴리텍Ⅱ대학 남인천캠퍼스의 학위과정 범위 공백으로 확정했다. 고위험 패널의 수동 검토 잔여는 0개다.

## 취업통계 후보 규칙

1. 2010–2022년 비어 있지 않은 OpenID별로 `(학과명, 단과대학명)` 완전 집합을 만든다.
2. 2023–2024년은 `(연도, 학교명, 본분교명, 시도명, 학교종류명)`별 완전 집합을 만든다.
3. 집합 크기 3 이상이고 하나의 과거 OpenID와만 정확히 일치할 때만 후보로 기록한다.
4. 같은 연도의 안전 결합 기준표에서 시도와 본분교 맥락을 확인한다.
5. 이 1단계 결과의 `_open_id_candidate`는 증거 라벨이며 원본·제한 패널과 후보 전용 기본 파생 파일에는 정식 `개방ID` 열을 만들지 않는다. 별도 승인 적용은 대학알리미 2개년 후보만 사용해 독립 `resolved` 파생 파일을 만든다.

## 결과

| 상태 | 학교·연도 | 원본 행 |
|---|---:|---:|
| 학과서명과 0101 맥락 일치 후보 | 30 | 272 |
| 학과서명 후보이나 0101 맥락 충돌 | 2 | 6 |
| 복수 OpenID와 정확히 일치 | 4 | 48 |
| 정확한 서명 없음 | 2,108 | 45,129 |
| 비교 서명 크기 3 미만 | 1,137 | 1,507 |
| 합계 | 3,281 | 46,962 |

후보로 등장한 과거 OpenID는 15개다. 이 숫자는 학교 수나 확정 연결 수가 아니다.

위 표는 제외 결정을 검증하기 위한 진단 결과다. 후보 상태와 관계없이 기존 OpenID 종단 패널 적격 행은 0개이고, 제외 행은 46,962개다. 독립 자료 안에서는 2023년과 2024년의 학교명·학과 단위 기술통계만 허용한다.

## 고위험 패널 처리

| 코드 | 데이터셋 | 경계 키 | 내부 공백 키 | 수동 확정 키·행 | 처리 |
|---|---|---:|---:|---:|---|
| 0202 | 대학입학전형기본계획_전문대학 | 26 | 0 | 0·0 | 미연결 보존, ID 수정 없음 |
| 0204 | 대학입학전형시행계획_전문대학 | 12 | 0 | 0·0 | 미연결 보존, ID 수정 없음 |
| 1102 | 도서관예산현황 | 35 | 0 | 0·0 | 미연결 보존, ID 수정 없음 |
| 1209 | 법인임원현황 | 66 | 1 | 1·16 | 동일 ID 유지, 행 보존, 대입·보간 없음 |

`1209`의 2019년 16행은 일련번호가 모두 유일하고 정확 중복이 0개다. 2020년 동일 OpenID 17행과 `일련번호·임원구분·성별` 조합 10개가 이어지며, [남인천캠퍼스 공식 연혁](https://kopo.ac.kr/namincheon/content.do?menu=7003)은 2019년 전문기술과정 운영과 2020년 2년제 학위과정 신설을 확인한다. 따라서 학교 폐교나 ID 변경이 아니라 `0101`의 학위과정 포함 범위 차이로 판정했다. 원본 16행과 OpenID를 그대로 보존하고 2019년 `0101` 값을 만들거나 인접 연도로 보간하지 않는다.

전체 요약 상태는 `complete_with_scope_exclusion`이다. 취업통계 형식 단절은 미해결 ID 대입 작업이 아니라 승인된 범위 제외 결정으로 종결했고, 고위험 패널 검토도 `complete`이며 수동 검토 필요 패널·키는 모두 0개다.

## 공식 교차표 후속 감사

2026년 8월 공식 EDSS 제공목록과 실제 2023·2024 원본 ZIP을 추가 대조했다. 공식 목록은 두 연도의 학교코드 제공 여부를 `Y`로 표시하지만, 실제 내부 CSV에는 `학교명`만 있고 `개방ID` 또는 다른 코드형 학교 열이 없다. EDSS 공식 검색에서도 `개방ID`와 `학교코드` 결과는 각각 0건이었다. 따라서 맥락 일치 후보 30개 중 공식 확정은 0개이며 기존 비대입 원칙을 유지한다. 자세한 근거는 `docs/edss_official_crosswalk_audit.md`에 기록했다.

## 대학알리미 재적학생수 교차검증

대학알리미 2023·2024년 학교 ID·학교명·학교구분·본분교·지역·재적학생수를 별도로 수집해 `0101`과 대조했다. 같은 대학알리미 학교 ID가 두 해 모두 문맥 안에서 정확히 하나의 OpenID를 선택하고 두 해의 ID가 같으며 역방향 중복도 없는 경우만 후보로 남겼다. 결과는 245개 학교–OpenID 후보이고 취업통계의 482개 학교·연도 식별자에 연결됐다. 기존 학과서명 후보와 겹치는 24개 학교·연도는 모두 동의했고 충돌은 0개였다.

이 교차검증은 기존 30개 학과서명 후보보다 범위를 넓히지만 공식 교차표를 대체하지 않는다. 별도 `resolved` 안전 파생 패널의 13,920개 행(29.6410%)·482개 학교연도(14.6906%)·242개 고유 OpenID는 추론 방법·상태·후보 경로와 함께 보존한다. 기존 값 덮어쓰기·충돌·후보 키 누락은 모두 0건이지만, 형식 단절 범위 결정에 따라 이 파일도 기존 OpenID 종단 분석에는 사용하지 않는 참고 산출물이다. 자세한 규칙은 `docs/edss_academyinfo_enrollment_crosswalk.md`에 기록했다.

## 산출물과 재현

- 안전 파생 집계: `data/processed/edss/derived/employment_2023_2024_school_department.csv.gz` (Git 제외)
- OpenID 적용 안전 파생 집계: `data/processed/edss/derived/employment_2023_2024_school_department_resolved.csv.gz` (Git 제외)
- 취업 후보 상태: `data/metadata/edss_employment_2023_2024_open_id_candidates.csv`
- 취업 형식 단절 범위 결정: `data/metadata/edss_employment_schema_break_decision.csv`
- 고위험 패널 수동 판정 입력: `data/metadata/edss_high_orphan_manual_decisions.csv`
- 고위험 패널 판정: `data/metadata/edss_high_orphan_panel_review.csv`
- 실행 요약과 체크섬: `data/metadata/edss_remaining_identity_gap_resolution.json`
- 실행 노트북: `notebooks/edss_remaining_identity_gap_resolution.ipynb`
- 공식 교차표 감사: `data/metadata/edss_official_crosswalk_audit.json`
- 공식 교차표 검산 노트북: `notebooks/edss_official_crosswalk_audit.ipynb`
- 대학알리미 학교 ID–OpenID 후보: `data/metadata/edss_academyinfo_open_id_candidates.csv`
- 대학알리미–취업 학교·연도 후보: `data/metadata/edss_employment_enrollment_open_id_candidates.csv`
- 대학알리미 교차검증 요약: `data/metadata/edss_academyinfo_open_id_match.json`
- 대학알리미 교차검증 노트북: `notebooks/edss_academyinfo_enrollment_crosswalk.ipynb`
- 대학알리미 2025년 대학원명·학과 교차검증: `docs/edss_academyinfo_graduate_name_coverage.md`
- 대학원명 교차검증 노트북: `notebooks/academyinfo_graduate_name_coverage.ipynb`
- 대학원 OpenID 추론 후보: `data/metadata/edss_graduate_open_id_candidates.csv`
- 대학원 OpenID 추론 요약: `data/metadata/edss_graduate_open_id_inference.json`
- 대학원 OpenID 추론 검산 노트북: `notebooks/graduate_open_id_inference.ipynb`
- OpenID 적용 감사: `data/metadata/edss_employment_open_id_application.json`
- OpenID 적용 검산 노트북: `notebooks/edss_employment_open_id_application.ipynb`

```bash
python3 scripts/resolve_edss_remaining_identity_gaps.py
```

`apply_edss_employment_open_id_candidates.py --approve-inferred-crosswalk`는 기존 추론 적용 결과를 재현할 때만 사용한다. 그 출력은 종단 패널 통합 대상이 아니다.

DuckDB 기본 조회 계층은 이 결정을 강제한다. `analysis.employment_legacy_2010_2022`는 2010–2022년만 포함하고, `analysis.employment_2023_2024_standalone`은 정식·후보 OpenID 열 없이 독립 기술통계만 제공한다. 이전 `analysis.employment_2023_2024_resolved` 뷰는 제거한다.
