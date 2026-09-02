# EDSS 연도 패널 데이터셋

기준일: 2026-09-01 (Asia/Seoul)

## 구축 결과

EDSS 개방데이터를 주 자료원으로 사용해 세 필수 분야의 265개 물리 데이터 단위, 278개 원본 ZIP을 233개 논리 주제 패널로 변환했다. 총행 수는 180,119,183행이고 압축 출력은 17,000,115,337 bytes다. 일반 패널은 232개이며 취업통계 1개는 제한 경로에 분리했다.

아래 표는 최초 우선 검토 15개 패널이다. 이 15개는 전체 빌드에서도 행 수와 SHA-256이 변하지 않았다. 전체 233개 목록은 `data/metadata/edss_panel_catalog.csv`를 기준으로 한다.

| 코드 | 주제 | 연도 | 행 | 원본 열 | 접근 구분 |
|---|---|---|---:|---:|---|
| 0101 | 고등교육학교개황 | 2009–2025 | 31,883 | 66 | 일반 패널 |
| 0103 | 대학교학생개황 | 2009–2025 | 333,376 | 67 | 일반 패널 |
| 0105 | 대학원학생개황 | 2009–2025 | 64,747 | 115 | 일반 패널 |
| 0305 | 신입생충원현황 | 2009–2025 | 467,149 | 21 | 일반 패널 |
| 0310 | 재적학생현황 | 2009–2025 | 441,474 | 17 | 일반 패널 |
| 0316 | 중도탈락학생현황 | 2009–2025 | 427,493 | 28 | 일반 패널 |
| 0502 | 전체교원대비전임교원현황 | 2009–2025 | 242,418 | 42 | 일반 패널 |
| 0503 | 전임교원1인당학생현황 | 2009–2025 | 22,083 | 15 | 일반 패널 |
| 0601 | 국내외학술지게재논문실적 | 2009–2025 | 201,445 | 20 | 일반 패널 |
| 0714 | 등록금현황 | 2009–2025 | 2,822,368 | 17 | 일반 패널 |
| 1014 | 연구비수혜실적 | 2009–2025 | 14,505 | 30 | 일반 패널 |
| 1016 | 장학금수혜현황 | 2009–2025 | 978,794 | 36 | 일반 패널 |
| 1026 | 현장실습운영현황 | 2011–2025 | 253,332 | 52 | 일반 패널 |
| 1207 | 기숙사수용현황 | 2009–2020, 2022–2025 | 14,730 | 19 | 일반 패널 |
| 0001 | 학생인적취업정보 | 2010–2024 | 7,324,949 | 167(연도 합집합) | 제한 패널 |

원본과 정제 패널은 재배포 조건과 민감도를 확인할 때까지 Git에서 제외한다. 공개 저장소에는 재수집·변환 코드, 원본 체크섬, 열 사전, 행 수, 품질검사 결과만 저장한다.

## 저장 구조와 행 추적

- 일반 패널: `data/processed/edss/panel/{출처}/{코드_데이터셋}/panel.csv.gz`
- 취업 제한 패널: `data/processed/edss/restricted/취업통계/0001_학생인적취업정보/panel.csv.gz`
- 주제별 프로필: 각 패널과 같은 폴더의 `profile.json`
- 전체 카탈로그: `data/metadata/edss_panel_catalog.csv`
- 필드 사전: `data/metadata/edss_panel_data_dictionary.csv`
- 전체 빌드 검증 노트북: `notebooks/edss_full_panel_build_validation.ipynb`
- 전체 키·내용 감사 노트북: `notebooks/edss_full_panel_key_audit.ipynb`
- 전체 키·내용 감사 보고서: `docs/edss_full_panel_key_audit.md`
- 전체 감사 요약: `data/metadata/edss_full_panel_key_audit.json`
- 최초 15개 패널 독립 검증: `data/metadata/edss_panel_validation.json`
- 미연결 키 요약: `data/metadata/edss_orphan_key_diagnosis.json`
- 미연결 키 목록: `data/metadata/edss_orphan_school_year_keys.csv`
- 안전 결합 기준표: `data/metadata/edss_school_year_bridge.csv`
- 기준표 검증 요약: `data/metadata/edss_school_year_bridge_summary.json`
- 2023–2024 취업 안전 집계: `data/processed/edss/derived/employment_2023_2024_school_department.csv.gz`
- 남은 식별자 공백 처리 요약: `data/metadata/edss_remaining_identity_gap_resolution.json`
- 남은 식별자 공백 검산 노트북: `notebooks/edss_remaining_identity_gap_resolution.ipynb`

원본 열은 이름과 값을 변경하지 않고 모두 문자열로 저장한다. 따라서 `개방ID`와 기타 코드의 선행 0, 원본 공란, `-` 등의 표기를 보존한다. 다음 열만 출처 추적을 위해 앞에 추가한다.

| 추가 열 | 의미 |
|---|---|
| `_source_provider`, `_source_area` | 제공기관과 EDSS 영역 |
| `_source_catalog_code`, `_source_dataset`, `_source_domn_code` | 원본 데이터셋 식별정보 |
| `_source_archive`, `_source_archive_sha256` | 원본 ZIP 경로와 체크섬 |
| `_source_member`, `_source_row_number` | ZIP 내부 파일과 원본 행 번호 |
| `_source_row_id` | 물리 원본 위치로 만든 결정적 행 ID |
| `_source_row_hash` | 원본 값 전체의 SHA-256 |
| `_panel_year` | 원본 `조사년도` 우선, 파일명 연도 보조 |

## 결합 키와 주의사항

일반 패널의 공통 결합 후보는 `조사년도`와 `개방ID`다. `개방ID`는 EDSS가 제공한 원본 명칭을 그대로 사용하며 공식 학교 ID로 임의 개명하지 않았다. 학과 단위 표에서는 `학과명`, 단과대학, 주야간, 학과특성 등 원본 차원을 추가해야 한다. 공식 학과 코드가 없는 표가 많으므로 학과명만으로 장기 동일 학과라고 확정해서는 안 된다.

`0101 고등교육학교개황`에는 31,883행과 30,556개의 `(연도, 개방ID)`가 있다. 이 후보키가 반복되는 키는 1,187개이고 최대 반복 수는 4다. 독립 재검산 결과 반복키 1,187개 모두에서 `본분교명`이 달랐고, `(연도, 개방ID, 본분교명)`은 31,883행 전체에서 유일했다. 따라서 이는 중복 오류가 아니라 본교·캠퍼스 grain이다. 다른 패널에는 `본분교명`이 대부분 없으므로 원시 `0101`에 직접 결합하면 행이 증식할 수 있다.

최초 우선 15개 주제의 고유 `(연도, 개방ID)`를 `0101`과 비교하면 데이터셋별 연결률은 99.30–100%다. 데이터셋별 미연결 키를 합하면 316건(일반 패널 305건, 취업통계 11건)이지만, 동일 키의 데이터셋 간 중복을 제거하면 76개 연도–ID 조합과 69개 `개방ID`다. 영향 행은 최초 15개 패널 13,640,746행 중 8,106행(0.0594%)이다. 이 수치는 전체 233개 패널 감사 결과로 확대 해석하지 않는다.

분석용 결합에는 원시 `0101` 대신 `edss_school_year_bridge.csv`를 사용한다. 이 기준표는 전체 패널에서 관측된 비어 있지 않은 `(연도, 개방ID)`마다 한 행이며, `0101`의 본분교·시도·지역·학교구분은 서로 다른 범주 목록으로만 보존한다. 2026-09-02 재생성에서 31,336개 키의 유일성과 233개 패널 180,119,183행의 left join 무증식이 확인됐다. `0101` 수치 지표는 임의 합산하거나 대표 캠퍼스에서 선택하지 않는다. 자세한 grain과 left join 규칙은 `docs/edss_school_year_bridge.md`에 기록했다.

76개 키를 동일 ID의 `0101` 관측기간과 비교하면 최초 등장 전 38개, 마지막 등장 후 32개, 내부 연도 공백 1개, 전 연도 미등장 5개다. 시간적 분류는 조사범위 차이의 증거일 뿐 신설·폐교·통폐합·ID 변경의 확정 근거가 아니다. 외부 학교명·학교상태 교차표로 검증하기 전에는 미연결 행을 삭제하거나 인접 연도 ID로 강제 매핑하지 않는다.

학교명은 일반 EDSS 패널에 대부분 없고 취업통계에만 있다. API는 원자료 대체가 아니라 다음 단계에서 학교 ID·학교명 교차표와 최신 상태를 검증하는 용도로 사용한다.

## 취업통계 구조 전환

취업통계는 2010–2022년 146열 개인형 자료와 2023–2024년 24열 학교·학과 집계형 자료가 하나의 논리 주제 안에 공존한다. 2023년부터 `개방ID`가 사라지고 `학교명`과 집계 지표가 제공된다. 2023–2024년 46,962행은 `개방ID`로 이전 연도나 다른 패널과 직접 연결할 수 없다.

개인형 자료에는 개인식별번호, 회사명 등 민감 가능 열이 있으므로 제한 패널로 분리했다. 공개 저장소에 원본·정제 행을 넣지 않으며, 향후 분석용 집계표를 만들 때도 필요한 학교·학과 수준 지표만 출력해야 한다.

2026-09-02 후속 실행에서 이 46,962행을 개인형 열이 없는 학교·학과 집계 파생 파일로 분리했다. 과거 OpenID별 학과·단과대 완전 집합과 일치한 학교·연도 후보 32개 중 30개는 같은 연도 `0101` 시도·본분교 맥락도 일치했고 2개는 충돌했다. 정식 `개방ID` 대입은 0건이며 후보는 공식 교차표를 확보하기 전까지 확정 연결로 사용하지 않는다. 자세한 규칙과 고위험 패널 검토는 `docs/edss_remaining_identity_gap_resolution.md`에 기록했다.

## 품질검사 결과

- 전체 233개 패널의 파일 존재·크기·카탈로그 SHA-256 일치 확인
- 카탈로그와 233개 프로필의 경로·행 수·크기·SHA-256 불일치 0건
- 최초 15개 패널은 전체 빌드 전후 행 수와 SHA-256 동일
- 전체 출력 행 수 합계 180,119,183행으로 원본 사전검사 합계와 일치
- 예상 연도 누락 0건; 기숙사 2021년 부재는 공식 제공 범위와 일치
- 구조가 깨진 CSV 행 0건
- 원본 전체 값이 완전히 같은 중복 행 0건
- 일반 패널의 `조사년도`와 `개방ID` 결측 0건
- 등록금 `학과명` 공란 6건 보존
- 취업통계 `개방ID` 결측 46,962건: 오류가 아니라 2023년 구조 전환에서 열 자체가 제거된 결과
- 취업통계 2023–2024년 안전 파생 집계 46,962행; 개인형 열 0개, 정식 `개방ID` 대입 0건
- high 패널 4개 중 3개는 시간 경계로 설명, `1209`의 내부 공백 1개·16행만 수동 검토 유지
- 자료형·단위·결측 기호의 공식 의미는 추측하지 않고 데이터 사전에 `공식 정의 미확인`으로 기록

전체 233개 패널·180,119,183행 재감사 결과 파일 폭, 행 ID, 행 ID 형식, 행 ID 중복, 패널 연도, 원본 연도 일치와 정규화 충돌 오류는 모두 0건이다. 다만 211개 패널은 `(연도, 개방ID)`보다 세분된 grain이고, 1개 취업 패널은 `개방ID`가 부분적으로만 존재한다. `0101` 미연결 키는 3,322건·82,959행이며 4개 패널은 미연결률이 1%를 넘는다. 원시 `0101` 결합은 기준 패널의 반복 키 때문에 229개 패널에서 이론상 30,192,638행을 추가 생성할 수 있다. 따라서 상태는 데이터 손상이 아니라 분석용 키·조인 규칙 검토가 필요한 `review_required`다. 자세한 결과는 `docs/edss_full_panel_key_audit.md`에 기록했다.

## 재현 명령

```bash
python3 scripts/build_edss_dataset.py \
  --inventory data/metadata/edss_full_rebuild_inventory.csv \
  --scan-profiles data/metadata/edss_full_rebuild_schema_scan.jsonl \
  --inspect-only
python3 scripts/build_edss_dataset.py \
  --inventory data/metadata/edss_full_rebuild_inventory.csv \
  --scan-profiles data/metadata/edss_full_rebuild_schema_scan.jsonl
python3 scripts/validate_edss_dataset.py
python3 scripts/diagnose_edss_orphan_keys.py
python3 scripts/build_edss_school_year_bridge.py
python3 scripts/resolve_edss_remaining_identity_gaps.py
python3 scripts/audit_edss_full_panel_keys.py --repo-root .
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

입력 ZIP의 경로와 SHA-256이 같고 기존 패널 체크섬이 정상이면 빌더는 다시 변환하지 않는다. 원본이 바뀌었거나 재생성이 필요할 때만 `--force`를 사용한다.
