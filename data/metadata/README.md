# Metadata

수집 파일명, EDSS 테이블명, 대상 연도, 다운로드 시각, 파일 크기, SHA-256, 행 수, 열 수, 처리상태를 기록합니다.

- `file_manifest.jsonl`: 실제 확보한 포털·대학알리미 원본 파일
- `edss_download_attempts.jsonl`: EDSS 연도 목록 확인 및 실제 다운로드 결과
- `edss_file_manifest.jsonl`: EDSS 원본 ZIP 278개의 체크섬, 수집 방식, 압축 내부 요약
- `edss_live_download_catalog.jsonl`: 포털 화면에서 확인한 세 필수 분야의 실제 다운로드 단위(`domnCd`) 목록
- `edss_full_collection_attempts.jsonl`: 전체 수집 실행의 성공·실패·파일 크기·SHA-256 누적 기록
- `edss_full_collection_status.csv`·`edss_full_collection_summary.json`: 기존 및 신규 원본의 ZIP 무결성, 체크섬, 누락 상태를 합친 검증 결과
- `edss_full_rebuild_inventory.csv`: 전체 265개 물리 다운로드 단위의 논리 테이블 키, 원본 ZIP 경로·연도·체크섬과 기존 패널 포함 여부
- `edss_full_rebuild_inventory_summary.json`: 전체 233개 논리 테이블과 기존 15개 패널의 범위 차이, 분야별 수량, 실제 파일·SHA-256 재검산 결과
- `edss_full_rebuild_schema_scan.jsonl`: 전체 265개 물리 단위의 ZIP·CSV 멤버, 관찰 연도, 원본 열, 헤더 변형, 인코딩, 행 수와 행 폭 검사 결과
- `edss_full_rebuild_schema_scan_summary.json`: 278개 ZIP·1,142개 CSV·180,119,183행의 전수 스캔 요약과 제공·관찰 연도 차이 4건
- `edss_{코드}_schema.json`: 물리 데이터셋별 연도·행 수·인코딩·원본 스키마. 취업통계는 물리 코드도 파일명에 포함한다.
- `edss_panel_catalog.csv`: 233개 논리 주제 패널의 경로, 행·열·연도와 출력 SHA-256
- `edss_panel_data_dictionary.csv`: 원본 필드명, 한글 표기, 문자열 저장형, 관찰 자료형, 단위·결측 정의 상태
- `edss_panel_quality_report.json`: 패널 빌드 중 수집한 구조·중복·식별자 품질 결과
- `edss_panel_validation.json`: 최초 15개 우선 패널의 독립 재검산과 학교연도 키 연결 결과. 전체 233개 출력의 행 내용 재감사는 후속 실행에서 갱신한다.
- `edss_full_panel_key_audit.json`·`.csv`: 전체 233개 패널·180,119,183행의 무결성, grain, `개방ID` 완전성, `0101` 연결 및 원시 조인 증식 위험 감사 결과
- `edss_full_panel_key_audit_by_year.csv`: 패널·연도별 행 수, `개방ID` 결측 행과 `0101` 미연결 행
- `edss_full_panel_orphan_school_year_keys.csv`: 전체 패널에서 관찰된 `0101` 미연결 학교연도 키 3,322건과 시간적 분류
- `edss_orphan_key_diagnosis.json`: `0101` 미연결 학교연도 키의 중복 제거, 시간적 분류와 영향 행 수
- `edss_orphan_school_year_keys.csv`: 미연결 고유 `(연도, 개방ID)` 76개의 데이터셋·기준 관측연도·분류
- `edss_school_year_bridge.csv`: 모든 패널의 비어 있지 않은 `(연도, 개방ID)`를 한 행씩 보존하고 `0101` 범주형 속성과 미연결 검토 상태를 연결한 기준표
- `edss_school_year_bridge_summary.json`: 기준표 grain·유일성·입력 체크섬·데이터셋별 left join 무증식 검증 결과
- `edss_employment_2023_2024_open_id_candidates.csv`: 취업통계 2023–2024년 학교·연도별 학과서명 후보와 `0101` 맥락 확인 상태. 정식 ID 교차표가 아니다.
- `edss_high_orphan_panel_review.csv`: 미연결률 1% 초과 4개 패널의 시간 경계·내부 공백 분류와 권장 처리
- `edss_remaining_identity_gap_resolution.json`: 취업 안전 집계와 high 패널 후속 검토의 행 수·상태·출력 체크섬
- `edss_official_crosswalk_audit.json`: 2026년 8월 공식 EDSS 제공목록의 학교코드 표시, 2023·2024 취업 ZIP 실제 헤더, 공식 검색 결과와 후보의 공식 확정 여부 대조
- `academyinfo_enrollment_manifest.jsonl`: 대학알리미 2023·2024년 학교코드·재적학생수 XML 756개의 경로·상태·크기·SHA-256
- `academyinfo_enrollment_collection.json`: 학교코드 754행과 숫자 재적학생수 671건의 수집·중복·체크섬 요약
- `edss_academyinfo_open_id_candidates.csv`: 대학알리미 학교 ID별 2개 연도 재적학생수와 EDSS OpenID 정확일치 후보. 공식 교차표가 아니다.
- `edss_employment_enrollment_open_id_candidates.csv`: 대학알리미 후보를 취업통계 2023–2024년 학교명에 연결한 학교·연도 후보와 학과서명 교차검증 상태
- `edss_academyinfo_open_id_match.json`: 245개 학교–OpenID 후보, 482개 취업 학교·연도 연결, 학과서명 동의 24개·충돌 0개와 후보 생성 단계의 비대입 불변식
- `edss_employment_open_id_application.json`: 승인된 추론 후보를 별도 안전 파생 패널에 적용한 행 수·학교연도·결측·충돌·체크섬 감사 결과
- `edss_priority_school_history_candidates.json`·`.csv`: 우선 검토 ID 6개의 학과 집합·행 서명·`0101` 연결 후보 비교 결과
- `edss_priority_school_history_validation.csv`: 통폐합·명칭 변경·학위과정 공백과 중복 ID를 공식 자료 및 뉴스로 교차검증한 수동 학교명 후보표
- `edss_priority_school_history_sources.json`: 학교이력 판정의 원본 파일·공식 자료·언론 교차검증 근거 목록
- `edss_catalog_inventory.csv`: 기준 Excel 7개 시트의 행 단위 인벤토리
- `source_inventory.csv`: EDSS 우선순위와 공공데이터포털 파일/API 수집 목록
- `edss_field_dictionary.csv`: 공식 원본 항목명 기반 데이터 사전
- `student_field_dictionary.csv`: StudentService 공식 응답 필드 사전
- `api_manifest.jsonl`: Open API 페이지별 수집시각, 상태, 행 수, 원본 경로와 체크섬
- `api_field_dictionary.csv`: 실제 StudentService 응답 필드의 의미·자료형·단위·결측 정의
- `student_service_validation.json`: 연세대학교 2008·2009·최신 지원 연도 비교 결과

JSONL은 실행 이력을 추가 기록합니다. 원본 파일은 `data/raw/`에서 변경하지 않으며 Git에는 넣지 않습니다.
