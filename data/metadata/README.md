# Metadata

수집 파일명, EDSS 테이블명, 대상 연도, 다운로드 시각, 파일 크기, SHA-256, 행 수, 열 수, 처리상태를 기록합니다.

- `file_manifest.jsonl`: 실제 확보한 포털·대학알리미 원본 파일
- `edss_download_attempts.jsonl`: EDSS 연도 목록 확인 및 실제 다운로드 결과
- `edss_file_manifest.jsonl`: EDSS 원본 ZIP 29개의 체크섬, 수집 방식, 압축 내부 요약
- `edss_{코드}_schema.json`: 물리 데이터셋별 연도·행 수·인코딩·원본 스키마. 취업통계는 물리 코드도 파일명에 포함한다.
- `edss_panel_catalog.csv`: 15개 논리 주제 패널의 경로, 행·열·연도와 출력 SHA-256
- `edss_panel_data_dictionary.csv`: 원본 필드명, 한글 표기, 문자열 저장형, 관찰 자료형, 단위·결측 정의 상태
- `edss_panel_quality_report.json`: 패널 빌드 중 수집한 구조·중복·식별자 품질 결과
- `edss_panel_validation.json`: 패널 전체 독립 재검산과 학교연도 키 연결 결과
- `edss_catalog_inventory.csv`: 기준 Excel 7개 시트의 행 단위 인벤토리
- `source_inventory.csv`: EDSS 우선순위와 공공데이터포털 파일/API 수집 목록
- `edss_field_dictionary.csv`: 공식 원본 항목명 기반 데이터 사전
- `student_field_dictionary.csv`: StudentService 공식 응답 필드 사전

JSONL은 실행 이력을 추가 기록합니다. 원본 파일은 `data/raw/`에서 변경하지 않으며 Git에는 넣지 않습니다.
