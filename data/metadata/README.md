# Metadata

수집 파일명, EDSS 테이블명, 대상 연도, 다운로드 시각, 파일 크기, SHA-256, 행 수, 열 수, 처리상태를 기록합니다.

- `file_manifest.jsonl`: 실제 확보한 포털·대학알리미 원본 파일
- `edss_download_attempts.jsonl`: EDSS 연도 목록 확인 및 실제 다운로드 결과
- `edss_catalog_inventory.csv`: 기준 Excel 7개 시트의 행 단위 인벤토리
- `source_inventory.csv`: EDSS 우선순위와 공공데이터포털 파일/API 수집 목록
- `edss_field_dictionary.csv`: 공식 원본 항목명 기반 데이터 사전
- `student_field_dictionary.csv`: StudentService 공식 응답 필드 사전

JSONL은 실행 이력을 추가 기록합니다. 원본 파일은 `data/raw/`에서 변경하지 않으며 Git에는 넣지 않습니다.
