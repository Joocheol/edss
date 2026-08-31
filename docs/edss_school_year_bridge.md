# EDSS 학교연도 안전 결합 기준표

기준일: 2026-09-01 (Asia/Seoul)

## 목적과 grain

`data/metadata/edss_school_year_bridge.csv`는 카탈로그에 등록된 15개 패널 중 하나 이상에서 관측된, 비어 있지 않은 `(_panel_year, 개방ID)`마다 정확히 한 행을 갖는 결합 기준표다. 다른 패널을 `0101 고등교육학교개황`의 학교·캠퍼스 속성과 결합할 때 원시 `0101`을 직접 조인해서 생기는 행 증식을 막는 것이 목적이다.

`0101`의 자연키는 `(_panel_year, 개방ID, 본분교명)`이다. `(연도, 개방ID)`만 사용하면 1,187개 키가 반복되므로, 원시 `0101`은 다른 패널의 학교연도 키에 대해 다대일 차원표가 아니다. 이 기준표는 학교연도별 범주형 속성의 서로 다른 값을 목록으로 보존할 뿐, 학생 수·교원 수·면적 등 `0101` 수치 지표를 포함하거나 합산하거나 임의의 캠퍼스 행에서 선택하지 않는다.

## 보존하는 정보

| 열 | 의미 |
|---|---|
| `_panel_year`, `개방ID` | 기준표의 유일키 |
| `_0101_exists` | 동일 학교연도 키가 `0101`에 존재하는지 여부 |
| `_0101_match_status` | `matched` 또는 미연결 시간 분류 |
| `_review_status` | 외부 교차검증 필요 상태. 전 기간 미등장과 내부 공백을 별도 값으로 유지 |
| `_0101_source_row_count` | 해당 학교연도의 원시 `0101` 행 수 |
| `_0101_branch_count`, `_0101_branch_names` | 서로 다른 비어 있지 않은 `본분교명` 수와 정렬 목록 |
| `_0101_province_count`, `_0101_provinces` | 서로 다른 비어 있지 않은 `시도명` 수와 정렬 목록 |
| `_0101_region_count`, `_0101_regions` | 서로 다른 비어 있지 않은 `지역명` 수와 정렬 목록 |
| `_0101_school_type_count`, `_0101_school_types` | 서로 다른 비어 있지 않은 `학교구분명` 수와 정렬 목록 |
| `_0101_campus_scope` | `single_campus`, `multiple_campuses`, `unknown`, `not_observed` |
| `_source_dataset_count`, `_source_catalog_codes`, `_source_row_count` | 해당 키를 가진 전체 패널의 카탈로그 코드와 행 수 |

목록 열은 중복을 제거하고 사전순으로 정렬한 뒤 `|`로 연결한다. 빈 값은 목록과 개수에서 제외하지만 `_0101_source_row_count`는 원본 행 전체를 세므로, 원본 행 수와 속성 목록의 수를 혼동하지 않는다.

## 미연결과 검토 상태

미연결 76개 키도 기준표에서 삭제하지 않는다. `_0101_exists=false`로 두고 다음 상태를 보존한다.

| `_0101_match_status` | `_review_status` | 처리 |
|---|---|---|
| `before_base_first_seen` | `unresolved_temporal_boundary` | 최초 관측 전 경계연도. 자동 보정 금지 |
| `after_base_last_seen` | `unresolved_temporal_boundary` | 마지막 관측 후 경계연도. 자동 보정 금지 |
| `internal_base_gap` | `external_crosscheck_required_internal_gap` | 내부 공백 1개를 별도 외부 검토 대상으로 유지 |
| `open_id_absent_all_years` | `external_crosscheck_required_absent_all_years` | 전 기간 미등장 5개를 별도 외부 검토 대상으로 유지 |

시간 분류는 관측 범위 차이를 나타낼 뿐 신설·폐교·통폐합·ID 변경을 증명하지 않는다. 미연결 키는 인접 연도의 동일 ID나 다른 ID에 강제 매핑하지 않는다.

## 구축 결과

- 기준표: 30,632행, 고유키 30,632개, 최대 키 반복 수 1
- `0101` 연결: 30,556개 키
- `0101` 미연결: 76개 키(최초 관측 전 38, 마지막 관측 후 32, 내부 공백 1, 전 기간 미등장 5)
- 캠퍼스 표시: 단일 29,369개, 복수 1,187개, `0101` 미관측 76개
- 입력: 15개 패널 13,640,746행, 체크섬 불일치 0개
- 결합 키 결측: 취업통계 구조 전환의 46,962행. 사실 패널에 그대로 보존
- `0101` 미연결 영향: 8,106행
- 기준표 left join: 출력 13,640,746행, 행 증식 0

새 기준표의 미연결 76개 키와 분류는 기존 `edss_orphan_school_year_keys.csv`의 키 집합 및 분류와 정확히 일치한다.

## 결합 규칙

1. 사실 패널을 왼쪽에 두고 `(_panel_year, 개방ID)` 두 열을 모두 사용해 이 기준표에 left join한다.
2. 기준표 키의 유일성이 깨지면 결합을 중단한다. 원시 `0101`에는 직접 조인하지 않는다.
3. `_0101_exists=false`인 행은 유지하고 `_0101_match_status`와 `_review_status`를 분석 필터와 품질 보고에 함께 사용한다.
4. 연도 또는 `개방ID`가 빈 원본 행도 사실 패널에서 유지한다. 빈 키를 하나의 가상 학교로 합친 기준표 행은 만들지 않는다.
5. 캠퍼스별 수치가 필요하면 `본분교명`을 포함한 원시 자연키 grain에서 별도 분석한다. 이 기준표의 목록 열로 수치를 배분하거나 합산하지 않는다.

SQL 형태는 다음과 같다.

```sql
SELECT f.*, b.* EXCLUDE (_panel_year, 개방ID)
FROM fact_panel AS f
LEFT JOIN school_year_bridge AS b
  ON f._panel_year = b._panel_year
 AND f.개방ID = b.개방ID;
```

## 자동 검증

`scripts/build_edss_school_year_bridge.py`는 다음을 검사하고 `data/metadata/edss_school_year_bridge_summary.json`에 결과를 기록한다.

- 카탈로그 15개 패널의 SHA-256 일치
- 기준표의 유일키, 빈 키, 최대 키 반복 수
- `0101` 존재 여부·미연결 분류·검토 상태·캠퍼스 표시의 내부 일관성
- 데이터셋별 원본 행 수, 키 결측 행 수, `0101` 연결·미연결 행 수
- 고유 기준표에 left join했을 때 원본 행 수가 유지되고 증식이 0인지 여부

단위 테스트는 복수 캠퍼스, 최초 관측 전, 마지막 관측 후, 내부 공백, 전 기간 미등장, 빈 결합 키를 포함한 합성 패널로 같은 규칙을 검증한다.

## 재현 명령

```bash
python3 scripts/build_edss_school_year_bridge.py
python3 -m unittest tests.test_build_edss_school_year_bridge -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

검산 노트북은 `notebooks/edss_school_year_bridge_validation.ipynb`다. 기준표와 요약 JSON을 다시 읽어 키 유일성, 상태별 개수, 외부 검토 6개 키, 데이터셋별 행 증식 0을 재확인한다.
