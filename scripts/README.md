# Scripts

EDSS 다운로드, 압축 해제, 원본 목록 작성, 표준화, 품질검사 도구를 이 폴더에 둡니다.

- `build_edss_dataset.py`: 직접 CSV ZIP, 전체연도 단일 CSV, 중첩 ZIP을 스트리밍으로 읽어 원본 문자열과 행 추적정보를 보존한 주제 패널을 만든다.
- `validate_edss_dataset.py`: 결과를 처음부터 다시 읽어 행 수, 체크섬, 연도 범위, 식별자 결측과 `0101` 기준 학교연도 연결률을 검산한다.
- `diagnose_edss_orphan_keys.py`: 데이터셋별 미연결 키의 중복을 제거하고, `0101` 관측기간 전·후·내부 공백·전 기간 미등장으로 분류해 키 목록과 영향 행 수를 기록한다.
- `build_edss_school_year_bridge.py`: 모든 패널의 비어 있지 않은 학교연도 키 합집합을 한 행씩 만들고, `0101`의 범주형 본분교·시도·지역 목록과 미연결 검토 상태를 보존한 안전 결합 기준표를 생성한다. 수치 지표는 집계하지 않는다.
- `validate_edss_priority_school_history.py`: 우선 검토 ID 6개의 학과 집합과 교원 행을 같은 연도 정상 ID와 비교해 통폐합·명칭 변경·중복 ID 검토용 후보표를 생성한다. 학교명 확정은 별도 공식 근거가 있을 때만 수행한다.
- `inspect_edss_zip.py`: 개별 EDSS ZIP의 헤더·연도·행 수·체크섬을 조사한다.
- `download_edss.py`: 공식 목록과 내부 파일 식별자를 확인한다. 브라우저 팝업 문맥이 필요한 실제 ZIP은 이 스크립트의 독립 HTTP 요청으로 받을 수 없다.
- `verify_edss_full_collection.py`: 세 필수 분야의 실시간 `domnCd` 목록을 기존·신규 다운로드 기록과 대조하고 ZIP 무결성·체크섬·누락·실패 상태를 검증한다.
