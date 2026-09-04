# EDSS DuckDB 조회 계층

## 결과

`data/processed/edss/restricted/edss_all.duckdb`는 EDSS 세 필수 분야의 233개 논리 패널을 하나의 파일에서 조회할 수 있게 만든 DuckDB 1.4.1 데이터베이스다. 패널마다 grain과 열 구성이 다르므로 서로 연결하지 않고 독립 테이블로 보존했다.

2026-09-02 전체 빌드 결과는 다음과 같다.

| 소스 | 스키마 | 패널 테이블 | 행 수 |
|---|---|---:|---:|
| 고등교육통계 | `higher_education` | 102 | 147,761,413 |
| 대학정보공시 | `university_disclosure` | 130 | 25,032,821 |
| 취업통계 | `employment` | 1 | 7,324,949 |
| 합계 |  | 233 | 180,119,183 |

DB 크기는 16,044,535,808 bytes이며 SHA-256은 `5a019a6844a8ce4960a4e736fccb497cf278b55b36e09ca46e405e24dd04a101`이다. 데이터 파일 자체는 Git에서 제외하고 빌드 감사 결과만 `data/metadata/edss_duckdb_build.json`에 보존한다.

## 스키마와 테이블

- `higher_education.panel_{catalog_code}`: 고등교육통계 패널
- `university_disclosure.panel_{catalog_code}`: 대학정보공시 패널
- `employment.panel_0001`: 전체 취업통계 제한 패널
- `employment.safe_2023_2024_resolved`: 개인형 열을 제거하고 추론 OpenID 적용 기록을 보존한 2023–2024 독립 참고 패널. 기존 OpenID 종단 분석에서는 제외한다.
- `meta.panel_catalog`: 원본 카탈로그와 DuckDB 테이블의 대응표
- `meta.load_manifest`: 적재 입력 SHA-256, 기대·실제 행과 열, 적재 시각
- `meta.database_summary`: 완전성 요약 한 행
- `analysis.panel_inventory`: 패널 카탈로그 조회 뷰
- `analysis.employment_2023_2024_resolved`: 안전 취업 패널 분석 뷰

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

-- 개인정보형 열이 없는 2023–2024 독립 참고 자료(종단 결합 금지)
SELECT _panel_year, 개방ID, 학교명, 학과명, 취업비율,
       _open_id_resolution_status
FROM analysis.employment_2023_2024_resolved
LIMIT 100;
```

패널을 결합할 때는 원시 `0101`을 바로 붙이지 말고 `docs/edss_school_year_bridge.md`의 학교연도 기준표와 grain 규칙을 따른다.

## 검증과 제한사항

전체 빌드 후 읽기 전용 연결에서 233개 테이블 각각을 카탈로그와 대조했다. 행·열 불일치는 0건이고 적재 합계는 180,119,183행이다. 원본 패널 열은 모두 `VARCHAR`이며 빈 문자열을 SQL `NULL`로 바꾸지 않는다. 안전 취업 패널은 46,962행이고 이 중 검토된 추론 교차표로 OpenID가 적용된 행은 13,920행, 여전히 빈 행은 33,042행이다.

전체 DB에는 2010–2022 취업통계의 민감 가능 개인형 열이 포함된다. 따라서 파일 전체를 외부 공유하거나 일반 분석 경로에 복사하지 않는다. `analysis.employment_2023_2024_resolved`는 2023–2024년 내부의 학교명·학과 단위 기술통계와 추론 감사에만 사용하고 과거 OpenID 패널과 결합하지 않는다. 과거 취업 원본 접근은 제한 환경에서 목적·권한을 확인한 뒤 수행한다.
