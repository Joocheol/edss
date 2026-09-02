# EDSS 공식 학교명–OpenID 교차표 감사

기준 실행일: 2026-09-02

## 결론

공식 EDSS 학교명–`개방ID` 교차표는 공개 자료에서 확인되지 않았다. 2026년 8월 공식 제공목록은 취업통계 2023–2024년의 `학교코드 제공여부`를 `Y`로 표시하지만, 체크섬을 확인한 실제 2023·2024년 원본 ZIP은 각각 24열이며 `학교명`만 있고 `개방ID` 또는 다른 코드형 학교 열은 없다. 따라서 이 공식 교차표 감사 단계에서 기존의 구조·0101 맥락 일치 후보 30개 중 공식 확정과 정식 `개방ID` 대입은 모두 0건이다. 이후 대학알리미 2개년 정확 일치 후보를 명시적으로 승인해 별도 안전 파생 패널에 적용했지만, 그 값은 공식 교차표가 아닌 추론값으로 표시한다.

이 불일치는 취업 집계 수치의 손상을 뜻하지 않는다. 다만 2023–2024년 자료를 과거 OpenID 기반 장기 패널에 연결하려는 용도에는 high 위험이다.

## 공식 자료 검사

공식 [EDSS 개방데이터 다운로드](https://www.edmgr.kr/edss/es/opd/odd/od/es_opd_oddod01_001) 화면에서 `개방용 에듀데이터 제공목록(26년 8월 기준).xlsx`를 내려받아 검사했다.

- 파일 크기: 155,208 bytes
- SHA-256: `47b15fd775cfa6d7744941c2dd6d6c603536764ca4ab191b091b3c83497aedc2`
- 시트·범위: `취업(1종), 평생(2종)!A4:I6`
- 2023–2024 주요 제공항목: 24개, `학교명` 포함, `개방ID` 미포함
- 2023–2024 `학교코드 제공여부`: `Y`

공식 통합검색에서 [`개방ID`](https://www.edmgr.kr/edss/es/mis/pas/ps/es_mis_pasps01_001?searchPvsnArtclCd4=%EA%B0%9C%EB%B0%A9ID)와 [`학교코드`](https://www.edmgr.kr/edss/es/mis/pas/ps/es_mis_pasps01_001?searchPvsnArtclCd4=%ED%95%99%EA%B5%90%EC%BD%94%EB%93%9C)는 각각 0건이었다.

## 실제 원본 대조

| 연도 | ZIP SHA-256 | CSV 열 | 학교명 | 개방ID | 기타 코드형 학교 열 |
|---|---|---:|---|---|---|
| 2023 | `1ace9e82663a816e00a66a35d2b76980e79c708d54abd390564d3d90dcdbe4eb` | 24 | 있음 | 없음 | 없음 |
| 2024 | `568086fa2f3d40f7711837f4b72aeada578022e64ed3ca9f1bdd8ebfbc7fa9f1` | 24 | 있음 | 없음 | 없음 |

두 파일은 외부 ZIP 안에 내부 ZIP이 있는 구조다. 실제 내부 CSV 헤더를 읽어 판정했으며 2023년 인코딩은 CP949, 2024년은 UTF-8 BOM이다.

## 판정과 사용 규칙

| 항목 | 결과 |
|---|---:|
| 학교·연도 전체 상태 | 3,281 |
| 구조·0101 맥락 일치 후보 | 30 |
| 후보 OpenID | 15 |
| 공식 교차표로 확정된 후보 | 0 |
| 정식 OpenID 대입 | 0 |

1. `_open_id_candidate`는 계속 검토용 증거 라벨로만 사용한다.
2. 후보를 원본·제한 패널이나 후보 전용 기본 파생 출력의 정식 `개방ID`로 복사하지 않는다. 별도 승인 적용 파일에서는 추론 상태와 출처 열을 반드시 함께 유지한다.
3. 장기 OpenID 분석에서는 2023–2024년을 분리하거나 학교명 grain으로만 사용한다.
4. 후보 승격에는 EDSS가 발행한 학교명–OpenID 교차표 또는 `학교코드 제공여부=Y`의 의미에 대한 공식 서면 설명이 필요하다.

## 산출물과 재현

- 감사 결과: `data/metadata/edss_official_crosswalk_audit.json`
- 감사 스크립트: `scripts/audit_edss_official_crosswalk.py`
- 검산 노트북: `notebooks/edss_official_crosswalk_audit.ipynb`
- 공식 제공목록 원본: `data/raw/edss/reference/edss_open_data_provider_list_2026-08.xlsx` (Git 제외)

```bash
python3 scripts/audit_edss_official_crosswalk.py
```
