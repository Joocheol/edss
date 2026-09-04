# Scripts

EDSS 다운로드, 압축 해제, 원본 목록 작성, 표준화, 품질검사 도구를 이 폴더에 둡니다.

- `build_edss_dataset.py`: 직접 CSV ZIP, 전체연도 단일 CSV, 중첩 ZIP을 스트리밍으로 읽어 원본 문자열과 행 추적정보를 보존한 주제 패널을 만든다. 전체 빌드는 인벤토리와 사전검사 JSONL을 대조하고, 대형 표의 SHA-256 행 해시를 디스크 분할해 메모리 사용량을 제한하며, 정상 기존 출력을 체크섬 검증 후 재사용한다.
- `validate_edss_dataset.py`: 결과를 처음부터 다시 읽어 행 수, 체크섬, 연도 범위, 식별자 결측과 `0101` 기준 학교연도 연결률을 검산한다.
- `audit_edss_full_panel_keys.py`: 233개 패널의 모든 행을 다시 읽어 파일·행 추적 무결성, 정규화 충돌, 후보키 반복, `0101` 미연결 키와 원시 조인 증식 위험을 패널·연도별로 감사한다. 패널별 캐시를 사용해 중단 후 재개할 수 있다.
- `diagnose_edss_orphan_keys.py`: 데이터셋별 미연결 키의 중복을 제거하고, `0101` 관측기간 전·후·내부 공백·전 기간 미등장으로 분류해 키 목록과 영향 행 수를 기록한다.
- `build_edss_school_year_bridge.py`: 모든 패널의 비어 있지 않은 학교연도 키 합집합을 한 행씩 만들고, `0101`의 범주형 본분교·시도·지역 목록과 미연결 검토 상태를 보존한 안전 결합 기준표를 생성한다. 수치 지표는 집계하지 않는다.
- `resolve_edss_remaining_identity_gaps.py`: 취업통계 2023–2024년을 개인형 열 없는 학교·학과 집계로 분리하고, 과거 학과서명과 같은 연도 `0101` 맥락에 기반한 검토 후보만 기록한다. 정식 `개방ID`는 대입하지 않으며 high 미연결 패널 4개의 처리 상태도 생성한다.
- `audit_edss_official_crosswalk.py`: 공식 EDSS 제공목록의 학교코드 제공 표시와 실제 2023·2024 취업 중첩 ZIP 내부 CSV 헤더를 대조한다. 공개 교차표가 없거나 메타데이터가 충돌하면 후보의 공식 확정을 0으로 유지한다.
- `collect_academyinfo_enrollment.py`: 대학알리미 2023·2024년 학교코드 목록과 학교별 재적학생수를 원본 XML로 재개 가능하게 수집하고, 인증정보 없이 체크섬·빈 응답·중복키를 기록한다.
- `collect_academyinfo_school_major.py`: 대학알리미 대학별 학과정보 API의 2025년 전체 학과를 재개 가능하게 수집하고, 일반·전문·특수대학원 부분집합과 대학원명·학위과정·입학정원·졸업자 수의 품질 요약을 기록한다. API에 대학원별 식별자가 없으므로 결과는 OpenID 검증 후보 자료로만 사용한다.
- `analyze_academyinfo_graduate_name_coverage.py`: 2025년 대학원명·학교종류·지역을 취업통계 2023–2024년 미연결 대학원 identity와 비교하고 학과 집합 겹침을 기록한다. 연도 차이와 식별자 부재 때문에 후보 OpenID는 생성하거나 대입하지 않는다.
- `infer_edss_graduate_open_id_candidates.py`: 대학알리미 이름·학과·주야간, EDSS 0402–0404 종류별 정상 ID 학과서명, 0101 지역·본분교, 0104 학과, 2개년 연속성을 결합해 대학원 OpenID 검토 후보를 만든다. 2022년 마스킹 백테스트로 임계값을 교정하고 연도 충돌·역방향 중복을 제외하며 정식 ID는 대입하지 않는다.
- `match_academyinfo_enrollment_open_ids.py`: 두 연도의 학교구분·지역·본분교·재적학생수가 각각 하나의 같은 EDSS OpenID에 정확히 일치하고 역방향도 유일할 때만 후보를 만든다. 취업 학교명에 연결하되 정식 ID는 대입하지 않는다.
- `apply_edss_employment_open_id_candidates.py`: 명시적 승인 플래그가 있을 때만 검토된 대학알리미 후보를 개인정보 열이 없는 별도 취업 파생 패널의 빈 `개방ID`에 적용한다. 기존 값·원본·제한 패널은 수정하지 않고 충돌 시 중단하며 적용 근거 열과 감사 JSON을 남긴다.
- `validate_edss_priority_school_history.py`: 우선 검토 ID 6개의 학과 집합과 교원 행을 같은 연도 정상 ID와 비교해 통폐합·명칭 변경·중복 ID 검토용 후보표를 생성한다. 학교명 확정은 별도 공식 근거가 있을 때만 수행한다.
- `inspect_edss_zip.py`: 개별 EDSS ZIP의 헤더·연도·행 수·체크섬을 조사한다.
- `download_edss.py`: 공식 목록과 내부 파일 식별자를 확인한다. 브라우저 팝업 문맥이 필요한 실제 ZIP은 이 스크립트의 독립 HTTP 요청으로 받을 수 없다.
- `verify_edss_full_collection.py`: 세 필수 분야의 실시간 `domnCd` 목록을 기존·신규 다운로드 기록과 대조하고 ZIP 무결성·체크섬·누락·실패 상태를 검증한다.
- `build_edss_full_rebuild_inventory.py`: 실시간 265개 `domnCd`와 두 다운로드 기록을 결합해 233개 논리 테이블의 전체 재구성 입력을 만들고, 현재 15개 패널의 범위와 파일·SHA-256 상태를 대조한다.
- `scan_edss_full_rebuild_inputs.py`: 전체 재구성 인벤토리의 명시적 ZIP 경로를 스트리밍으로 읽어 CSV 멤버·행 수·헤더 변형·관찰 연도·행 폭 오류를 물리 단위별로 기록한다.
- `build_edss_duckdb.py`: 233개 이질적 패널을 소스별 독립 테이블로 하나의 제한 DuckDB에 재개 가능하게 적재한다. 업무열 수와 공통 출처열 12개를 분리 검증하고, 취업통계 조회 계층·학교–연도 핵심 마트·취업 학교–연도 사전 집계와 무증식 결합 뷰를 함께 만든다.

전체 233개 패널 빌드는 저장소 루트에서 다음과 같이 실행한다.

```bash
python3 scripts/build_edss_dataset.py \
  --inventory data/metadata/edss_full_rebuild_inventory.csv \
  --scan-profiles data/metadata/edss_full_rebuild_schema_scan.jsonl
python3 scripts/audit_edss_full_panel_keys.py --repo-root .
```

전체 패널을 DuckDB 조회 계층으로 만들려면 다음을 실행한다. 정상 적재 테이블은 SHA-256·행·열을 확인한 뒤 건너뛰므로 중단 후 같은 명령으로 재개할 수 있다.

```bash
uv run --offline --with duckdb==1.4.1 python scripts/build_edss_duckdb.py
```
