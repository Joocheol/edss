# 대학알리미 재적학생수 기반 EDSS OpenID 후보

기준 실행일: 2026-09-02

## 결론

대학알리미 2023·2024년 재적학생수와 EDSS `0101 고등교육학교개황`을 학교구분·지역·본분교 문맥 안에서 대조해 245개 대학알리미 학교 ID와 245개 EDSS OpenID의 양방향 1:1 후보를 만들었다. 이 후보를 취업통계 2023–2024년 학교명에 연결하면 482개 학교·연도 식별자를 덮는다. 기존 학과서명 후보와 겹치는 24개 학교·연도는 모두 같은 OpenID에 동의했고 충돌은 0개였다.

이 결과는 공식 교차표가 아니라 서로 독립적인 공개 통계의 2개 연도 완전 일치에 기반한 고신뢰 후보다. 후보 검토 승인 후 원본·제한 패널과 후보 전용 파생 파일은 보존하고, 별도 안전 파생 패널의 `개방ID`에만 적용했다. 적용값은 추론 교차표임을 나타내는 근거 열을 함께 가지며 공식 EDSS 교차표로 표현하지 않는다.

## 원자료와 grain

대학알리미 Open API에서 다음 두 서비스를 사용했다.

- `BasicInformationService_2/getUniversityCode`: 공시연도별 학교 ID, 학교명, 학교구분, 학교유형, 본분교, 지역
- `StudentService/getComparisonEnrolledStudentCrntSt`: 학교 ID·공시연도별 재적학생 현황 지표 `indctId=9`, 값 `indctVal1`

2023년 학교·캠퍼스 코드 378개 중 숫자 재적학생수 응답은 336개, 2024년 376개 중 숫자 응답은 335개였다. 정상 빈 응답은 각각 42개와 41개로 0과 구분했다. 두 해의 학교 ID 합집합은 380개다.

EDSS 비교 대상은 `0101 고등교육학교개황`의 2023·2024년 행이다. `개방ID`는 같은 연도에 여러 캠퍼스 행을 가질 수 있으므로 다음 5개 필드를 함께 비교했다.

1. 연도
2. 학교구분
3. 시도
4. 본분교
5. 재적학생수

대학알리미 지역 `전남광주`는 EDSS의 `전남` 또는 `광주`만 허용한다. 다른 지역은 정규화 후 정확히 일치해야 한다. 대학알리미 `제2캠퍼스`는 EDSS `분교`와 비교한다.

## 후보 규칙

하나의 대학알리미 학교 ID를 EDSS OpenID 후보로 기록하려면 다음 조건을 모두 만족해야 한다.

1. 2023년과 2024년 재적학생수가 모두 숫자다.
2. 각 연도에서 학교구분·허용 지역·본분교·재적학생수 조합이 정확히 하나의 EDSS OpenID만 선택한다.
3. 두 연도가 같은 EDSS OpenID를 선택한다.
4. 하나의 EDSS OpenID를 둘 이상의 대학알리미 학교 ID가 선택하지 않는다.

어느 조건이든 실패하면 후보를 비운다. 근사값, 최근접값, 허용오차는 사용하지 않았다.

## 결과

| 검사 | 결과 |
|---|---:|
| 대학알리미 학교 ID 합집합 | 380 |
| 숫자 재적학생수 학교·연도 | 671 |
| EDSS OpenID가 단일하게 정확 일치한 학교·연도 | 507 |
| 2개 연도가 같은 OpenID에 정확 일치한 학교 | 245 |
| 후보의 서로 다른 OpenID | 245 |
| 역방향 OpenID 중복 | 0 |
| 취업통계 학교·연도 연결 | 482 |
| 취업통계 이름 연결 미완료 | 8 |
| 기존 학과서명 후보와 동의 | 24 |
| 기존 학과서명 후보와 충돌 | 0 |
| 안전 파생 패널 적용 학교·연도 | 482 |
| 안전 파생 패널 적용 행 | 13,920 |
| 안전 파생 패널 적용 고유 OpenID | 242 |
| 적용 후 `개방ID` 미해결 행 | 33,042 |
| 기존 비어 있지 않은 ID 덮어쓰기·충돌 | 0 |

취업통계 이름 연결은 공백·괄호·`국립` 접두어를 정규화하고 알려진 캠퍼스 표기 차이만 별도 별칭으로 관리한다. 이름이 바뀌었거나 학부 취업행이 없는 8개 학교·연도는 자동 연결하지 않았다.

## 품질 판단

- **발견:** 245개 학교는 두 해 모두 수치가 정확히 일치하고 동일 OpenID를 가리킨다.
- **근거:** 단일연도 유일 일치 507개, 2개 연도 동일 ID 245개, 역방향 중복 0개, 독립 학과서명 24/24 동의.
- **영향:** 취업통계 2023–2024년의 482개 학교·연도에 검토 가능한 OpenID 후보가 생겼다.
- **심각도:** 중간. 원본 수치는 손상되지 않았지만 공식 교차표가 아니므로 canonical ID로 승격하면 식별 오류 위험이 남는다.
- **신뢰도:** 후보 생성 규칙과 관찰 결과는 높음. 공식 학교 ID라는 해석의 신뢰도는 공식 확인 전까지 제한됨.
- **처리:** 후보 파일과 원본·제한 패널은 그대로 유지한다. 명시적 승인 플래그를 거쳐 별도 안전 파생 패널에만 적용하고, 13,920개 적용 행에는 방법·상태·후보 파일 경로를 기록한다.
- **잔여 위험:** 전체 46,962행 중 33,042행(70.3590%)은 승인 후보가 없어 계속 미해결이다. 적용된 ID도 공식 교차표가 아니라 검토 승인된 추론값이다.

## 산출물과 재현

- 대학알리미 수집 기록: `data/metadata/academyinfo_enrollment_manifest.jsonl`
- 대학알리미 수집 요약: `data/metadata/academyinfo_enrollment_collection.json`
- 학교 ID–OpenID 후보: `data/metadata/edss_academyinfo_open_id_candidates.csv`
- 취업 학교·연도 후보: `data/metadata/edss_employment_enrollment_open_id_candidates.csv`
- 매칭 품질 요약: `data/metadata/edss_academyinfo_open_id_match.json`
- 실행 노트북: `notebooks/edss_academyinfo_enrollment_crosswalk.ipynb`
- ID 적용 안전 파생 패널: `data/processed/edss/derived/employment_2023_2024_school_department_resolved.csv.gz` (Git 제외)
- ID 적용 감사: `data/metadata/edss_employment_open_id_application.json`
- ID 적용 검산 노트북: `notebooks/edss_employment_open_id_application.ipynb`

```bash
python3 scripts/collect_academyinfo_enrollment.py --years 2023 2024
python3 scripts/match_academyinfo_enrollment_open_ids.py
python3 scripts/apply_edss_employment_open_id_candidates.py --approve-inferred-crosswalk
```

원본 XML과 합친 CSV는 `data/raw/`에 보존하며 Git에 커밋하지 않는다. 인증키, 요청 URL의 쿼리 문자열, 쿠키는 메타데이터와 로그에 기록하지 않는다.
