# EDSS 학교–연도 핵심 분석 마트

기준 실행일: 2026-09-04

## 결론

`analysis.school_year_core_2010_2022`는 2010–2022년 EDSS 학교–연도 24,044개를 `(연도, OpenID)` 한 행으로 제공한다. `edss_school_year_bridge.csv`를 모집단으로 사용하므로 `0101 고등교육학교개황`에 없는 521개 키도 삭제하지 않는다. 연결된 23,523개 키에는 12개 규모 지표를 캠퍼스 행에서 합산하고, 미연결 키의 지표는 `NULL`로 유지한다.

원시 `0101`의 24,304행을 직접 조인하지 않고 먼저 23,523개 학교–연도로 집계하므로 복수 캠퍼스 710개 키에서도 행이 늘어나지 않는다. 마트 키 중복·공란, 기준표 left join 증식, 수치 파싱 오류, 음수, 여성 부분집합 위반, 원천–마트 합계 차이는 모두 0건이다.

## Grain과 모집단

- 단위: `(_panel_year, 개방ID)` 한 행
- 기간: 2010–2022년
- 모집단: 학교연도 기준표의 해당 기간 24,044개 키
- `0101` 연결: 23,523개
- `0101` 미연결: 521개
- 복수 캠퍼스: 710개
- 취업통계 2023–2024년: 포함하지 않음

`0101` 미연결 키는 `_0101_exists=false`와 기존 시간 범위·검토 상태를 그대로 가진다. 학교 속성이나 수치를 인접 연도에서 가져오지 않는다.

## 수치 집계 규칙

다음 12개 `0101` 지표는 비어 있지 않은 값이 정수로 해석되고 0 이상인지 확인한 뒤 학교연도별로 합산한다.

| 마트 열 | 원천 열 | 집계 |
|---|---|---|
| `enrolled_student_count` | `고등교육학교_재적학생수` | 캠퍼스 합계 |
| `female_enrolled_student_count` | `고등교육학교_재적여학생수` | 캠퍼스 합계 |
| `entrant_count` | `고등교육학교_입학생수` | 캠퍼스 합계 |
| `female_entrant_count` | `고등교육학교_여자입학생수` | 캠퍼스 합계 |
| `graduate_count` | `고등교육학교_졸업생수` | 캠퍼스 합계 |
| `female_graduate_count` | `고등교육학교_여자졸업생수` | 캠퍼스 합계 |
| `faculty_count` | `고등교육학교_교원수` | 캠퍼스 합계 |
| `female_faculty_count` | `고등교육학교_여자교원수` | 캠퍼스 합계 |
| `staff_count` | `고등교육학교_사무직원수` | 캠퍼스 합계 |
| `female_staff_count` | `고등교육학교_여자사무직원수` | 캠퍼스 합계 |
| `building_area` | `고등교육학교_건물면적` | 캠퍼스 합계 |
| `department_count` | `고등교육학교_학과수` | 캠퍼스 합계 |

0은 관측된 0으로 보존한다. `0101` 미관측은 0으로 바꾸지 않고 `NULL`로 둔다. 여성 학생·입학생·졸업생·교원·직원 수가 각각 전체 수를 넘으면 빌드를 중단한다.

## 범주형 속성

본분교·시도·지역·학교구분은 원시 캠퍼스 한 행을 임의로 대표값으로 선택하지 않는다. 기준표가 보존한 서로 다른 값의 개수와 `|` 구분 목록을 그대로 제공한다. `campus_scope=multiple_campuses`인 710개 학교연도는 목록 열과 합산 수치를 함께 사용해야 한다.

## 안전한 사용법

```sql
SELECT _panel_year, school_types, provinces,
       sum(enrolled_student_count) AS enrolled_students
FROM analysis.school_year_core_2010_2022
WHERE _0101_exists = 'true'
GROUP BY _panel_year, school_types, provinces
ORDER BY _panel_year, school_types, provinces;
```

다른 패널을 결합할 때는 그 패널을 필요한 분석 grain으로 먼저 집계한 후 `(_panel_year, 개방ID)`로 이 마트에 left join한다. 학과·교원유형 등 더 세분된 행을 그대로 연결하면 원래 패널의 grain이 사라지므로 금지한다.

## 산출물과 재현

- DuckDB 테이블: `analysis.school_year_core_2010_2022`
- 기준표 테이블: `meta.school_year_bridge`
- 빌드 감사: `data/metadata/edss_duckdb_build.json`
- 데이터 사전: `data/metadata/edss_school_year_core_data_dictionary.csv`
- 검산 노트북: `notebooks/edss_school_year_core_mart_validation.ipynb`

```bash
uv run --offline --with duckdb==1.4.1 python scripts/build_edss_duckdb.py
```

DuckDB 파일은 제한 자료이며 Git에 커밋하지 않는다. 코드·데이터 사전·감사 결과·문서·검산 노트북만 버전 관리한다.
