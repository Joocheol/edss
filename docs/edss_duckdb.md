# EDSS DuckDB 조회 계층

## 결과

`data/processed/edss/restricted/edss_all.duckdb`는 EDSS 세 필수 분야의 233개 논리 패널을 하나의 파일에서 조회할 수 있게 만든 DuckDB 1.4.1 데이터베이스다. 패널마다 grain과 열 구성이 다르므로 서로 연결하지 않고 독립 테이블로 보존했다.

2026-09-04 전체 재검증 결과는 다음과 같다.

| 소스 | 스키마 | 패널 테이블 | 행 수 |
|---|---|---:|---:|
| 고등교육통계 | `higher_education` | 102 | 147,761,413 |
| 대학정보공시 | `university_disclosure` | 130 | 25,032,821 |
| 취업통계 | `employment` | 1 | 7,324,949 |
| 합계 |  | 233 | 180,119,183 |

DB 크기는 16,080,711,680 bytes이며 SHA-256은 `3d200cfb90b6118adfe9adf5d1f1405b9cc32acc01ef6b41332f66250ceee53a`이다. 데이터 파일 자체는 Git에서 제외하고 빌드 감사 결과만 `data/metadata/edss_duckdb_build.json`에 보존한다.

## 스키마와 테이블

- `higher_education.panel_{catalog_code}`: 고등교육통계 패널
- `university_disclosure.panel_{catalog_code}`: 대학정보공시 패널
- `employment.panel_0001`: 전체 취업통계 제한 패널
- `employment.safe_2023_2024_standalone`: 개인형 열과 정식·후보 OpenID 열을 모두 제거한 2023–2024 독립 참고 패널
- `meta.panel_catalog`: 원본 카탈로그와 DuckDB 테이블의 대응표
- `meta.load_manifest`: 적재 입력 SHA-256, 기대·실제 행과 열, 적재 시각
- `meta.database_summary`: 완전성 요약 한 행
- `analysis.panel_inventory`: 패널 카탈로그 조회 뷰
- `analysis.employment_legacy_2010_2022`: 연도가 2010–2022로 고정되고 OpenID 결측이 없는 제한 종단 분석 뷰
- `analysis.employment_2023_2024_standalone`: OpenID 열이 없는 2023–2024 학교·학과 기술통계 뷰
- `analysis.school_year_core_2010_2022`: 기준표 24,044개 학교연도와 `0101` 규모 지표 12개를 결합한 유일키 분석 마트
- `analysis.employment_school_year_2010_2022`: 제한 취업통계 7,277,987행을 7,058개 학교연도와 11개 보고 지표로 사전 집계한 유일키 마트
- `analysis.school_year_core_with_employment_2010_2022`: 핵심 마트에 취업 학교연도 마트를 left join한 24,044행 무증식 뷰

이전 `analysis.employment_2023_2024_resolved` 뷰와 `employment.safe_2023_2024_resolved` 테이블은 기본 조회 계층에서 제거한다. 추론 적용 파일은 파일 기반 감사 산출물로만 보존한다.

취업 마트는 2022년 537개 학교의 집계 벡터가 2021년과 전부 같음을 감지해 해당 연도를 시계열 비교 부적격으로 표시한다. `진학자수`가 전부 0인 2016–2019년도 별도 품질 상태를 가지며, 분모 정의가 안정적이지 않으므로 공식 취업률은 생성하지 않는다.

동일한 카탈로그 코드가 출처마다 반복될 수 있으므로 반드시 스키마까지 포함한 이름을 사용한다. 카탈로그의 `domain_column_count`는 원래 업무열 수이고 `loaded_column_count`는 공통 출처 추적열 12개를 포함한 실제 테이블 열 수다.

## 빌드와 재개

저장소 루트에서 다음 명령을 실행한다.

```bash
uv run --offline --with duckdb==1.4.1 python scripts/build_edss_duckdb.py
```

빌더는 임시 테이블을 완전히 적재해 행·열을 확인한 뒤 트랜잭션으로 기존 테이블을 교체한다. `meta.load_manifest`의 입력 SHA-256과 실제 행·열이 현재 카탈로그와 같으면 그 테이블을 건너뛴다. 중단 후 같은 명령을 다시 실행하면 완료된 테이블은 재사용한다.

## 기본 조회

```sql
-- 전체 패널 목록
SELECT source, catalog_code, dataset, schema_name, table_name,
       row_count, domain_column_count, loaded_column_count
FROM meta.panel_catalog
ORDER BY source, catalog_code;

-- 고등교육학교개황
SELECT *
FROM higher_education.panel_0101
WHERE 조사년도 = '2024'
LIMIT 100;

-- 2010–2022 OpenID 종단 분석(제한 자료)
SELECT _panel_year, 개방ID, count(*) AS rows
FROM analysis.employment_legacy_2010_2022
GROUP BY _panel_year, 개방ID
LIMIT 100;

-- 개인정보형·OpenID 열이 없는 2023–2024 독립 참고 자료
SELECT _panel_year, 학교명, 학과명, 취업비율
FROM analysis.employment_2023_2024_standalone
LIMIT 100;
```

패널을 결합할 때는 원시 `0101`을 바로 붙이지 말고 `docs/edss_school_year_bridge.md`의 학교연도 기준표와 grain 규칙을 따른다.

## 검증과 제한사항

전체 빌드 후 읽기 전용 연결에서 233개 테이블 각각을 카탈로그와 대조한다. 원본 패널 열은 모두 `VARCHAR`이며 빈 문자열을 SQL `NULL`로 바꾸지 않는다. 취업 조회 계층은 2010–2022년 종단 뷰 7,277,987행과 2023–2024년 독립 뷰 46,962행으로 분리한다. 종단 뷰의 2023–2024년 행과 OpenID 결측은 각각 0건이어야 하고, 독립 뷰에는 `개방ID`와 `_open_id_candidate` 열이 모두 없어야 한다.

전체 DB에는 2010–2022 취업통계의 민감 가능 개인형 열이 포함된다. 따라서 파일 전체를 외부 공유하거나 일반 분석 경로에 복사하지 않는다. `analysis.employment_2023_2024_standalone`은 2023–2024년 내부의 학교명·학과 단위 기술통계에만 사용하며 과거 OpenID 패널과 결합하지 않는다. 과거 취업 원본과 `analysis.employment_legacy_2010_2022` 접근은 제한 환경에서 목적·권한을 확인한 뒤 수행한다.
