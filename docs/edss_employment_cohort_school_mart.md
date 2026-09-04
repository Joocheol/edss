# EDSS 취업통계 최종 코호트 학교 마트

기준 실행일: 2026-09-04

## 결과

`analysis.employment_cohort_school_2010_2020`을 최종 취업 코호트 분석 마트로 생성했다. 승인된 11개 원천 파일의 6,167,230개 졸업자 레코드를 2010–2020년 11개 졸업 코호트와 OpenID별 5,969개 유일키로 집계한다. 개인식별번호·회사명·논문명 등 개인형 열은 포함하지 않는다.

핵심 학교 마트와 결합한 `analysis.school_year_core_with_employment_cohort_2010_2020`은 20,226행·20,226개 유일키를 유지한다. 취업 자료가 연결된 행은 5,969개, 연결되지 않은 행은 14,257개이며 조인 증식과 취업 키 누락은 모두 0건이다.

## 원천 선택

| 졸업 코호트 | 선택 파일 연도 | 기준일 | 학교 키 | 원천 레코드 | 선택 근거 |
|---:|---:|---|---:|---:|---|
| 2010 | 2010 | 2010-06-01 | 513 | 539,071 | 고유 코호트 |
| 2011 | 2011 | 2011-06-01 | 543 | 559,000 | 고유 코호트 |
| 2012 | 2012 | 2012-06-01 | 543 | 566,374 | 고유 코호트 |
| 2013 | 2013 | 2013-06-01 | 537 | 555,141 | 고유 코호트 |
| 2014 | 2015 | 2014-12-31 | 552 | 557,234 | 전환기 연말 파동 선택 |
| 2015 | 2016 | 2015-12-31 | 554 | 576,023 | 고유 코호트 재키잉 |
| 2016 | 2017 | 2016-12-31 | 557 | 580,695 | 고유 코호트 재키잉 |
| 2017 | 2018 | 2017-12-31 | 552 | 574,009 | 고유 코호트 재키잉 |
| 2018 | 2019 | 2018-12-31 | 544 | 555,808 | 고유 코호트 재키잉 |
| 2019 | 2020 | 2019-12-31 | 537 | 550,354 | 고유 코호트 재키잉 |
| 2020 | 2021 | 2020-12-31 | 537 | 553,521 | 정확 반복 중 첫 제공본 선택 |

2014 파일 557,236행은 같은 2014 졸업 코호트의 6월 1일 파동이므로 원천 계층에는 보존하되 최종 마트에서 제외했다. 2015 파일 557,234행은 같은 코호트의 12월 31일 파동이다. KEDI 전환표가 두 기준일과 조사 방법을 명시하고, KEDI의 연말 조사 발표 총계가 2015 파일의 졸업자 수 및 `취업자수`와 맞으므로 후속 계열과 동일한 연말 파동을 선택했다. [KEDI 조사 전환표](https://kess.kedi.re.kr/publ/fileDownload.do?fileSeq=1032483&publItemId=79838), [KEDI 2014년 12월 31일 조사 발표](https://www.kedi.re.kr/khome/main/announce/selectBroadAnnounceForm.do?article_sq_no=30069&board_sq_no=3&currentPage=0&doc_use_yn=N&selectTp=0)

2022 파일 553,521행은 2021 파일과 졸업월 분포 및 537개 학교의 원천 행 수·11개 집계 지표가 모두 같으므로 제외했다. 공식 2021년 조사는 2020년 8월·2021년 2월 졸업자를 대상으로 하므로 2021 파일을 2020 졸업 코호트로 재키잉한다. [KEDI 2021년 취업통계연보](https://kess.kedi.re.kr/publ/fileDownload.do?fileSeq=1064949&publItemId=95638)

## 비교 구간

최종 마트는 기준일 차이를 명시적으로 보존한다.

- `june_1_pre_unification`: 2010–2013 코호트
- `december_31_transition_selected`: 2014 코호트
- `december_31_post_unification`: 2015–2020 코호트

따라서 2010–2013 내부 또는 2014–2020 내부 비교는 같은 기준일 구간으로 해석할 수 있지만, 2013↔2014 변화에는 6월 1일에서 12월 31일로 바뀐 제도 효과가 섞인다. 이 경계를 순수한 취업시장 변화로 해석하지 않는다. 지표 정의 자체도 시기에 따라 확대되었으므로 공식 취업률을 별도로 계산하지 않는다.

## 검증 결과

| 검사 | 결과 |
|---|---:|
| 최종 마트 행 수 | 5,969 |
| `(employment_cohort_year, 개방ID)` 유일키 | 5,969 |
| 선택 원천 레코드 합계 | 6,167,230 |
| 코호트 수 | 11 |
| 공란 키 | 0 |
| 중복 키 | 0 |
| 핵심 마트 미연결 취업 키 | 0 |
| 결합 뷰 행 수 | 20,226 |
| 결합 뷰 유일키 | 20,226 |
| 조인 증식 | 0 |
| 2014·2022 제외 파일에서 유입된 행 | 0 |

11개 보고 지표 각각의 선택 원천 합계와 코호트 마트 합계가 정확히 일치한다. `reported_graduate_count` 합계는 6,167,230, `reported_employed_count` 합계는 3,254,755다. 진학자수는 원천 파일 2016–2019, 즉 재키잉 후 2015–2018 코호트에서 전부 0이므로 `further_study_quality_status='all_zero_source_field'`인 행은 진학 분석에서 제외한다.

## 사용법

```sql
-- 최종 취업 코호트 학교 마트
SELECT employment_cohort_year,
       employment_source_panel_year,
       employment_reference_date,
       employment_comparability_regime,
       개방ID,
       reported_graduate_count,
       reported_employed_count
FROM analysis.employment_cohort_school_2010_2020;

-- 학교 규모 지표와 결합된 최종 코호트 뷰
SELECT _panel_year AS cohort_year,
       개방ID,
       enrolled_student_count,
       employment_source_panel_year,
       employment_reported_graduate_count,
       employment_reported_employed_count
FROM analysis.school_year_core_with_employment_cohort_2010_2020
WHERE _employment_cohort_exists = 'true';
```

파일 연도 자체를 감사할 때만 `analysis.employment_school_year_2010_2022`를 사용한다. 코호트 추세에는 `_panel_year`를 직접 쓰지 않는다. 취업 자료 미연결 행의 지표는 `NULL`이며 0으로 대체하지 않는다.

## 재현 자료

- 빌더: `scripts/build_edss_duckdb.py`
- 코호트 감사: `scripts/audit_edss_employment_cohort_years.py`
- 승인 매핑: `data/metadata/edss_employment_cohort_year_audit.csv`
- 데이터 사전: `data/metadata/edss_employment_cohort_school_data_dictionary.csv`
- 빌드 감사: `data/metadata/edss_duckdb_build.json`
- 검산 노트북: `notebooks/edss_employment_cohort_year_audit.ipynb`
- 합성 회귀 테스트: `tests/test_build_edss_duckdb.py`

연속 추세 그래프는 넣지 않았다. 2013↔2014 기준일 단절을 시각적으로 한 계열처럼 연결하면 잘못된 해석을 유도할 수 있어, 선택 매핑과 검증 수치를 정확한 표로 제시하는 편이 안전하다.
