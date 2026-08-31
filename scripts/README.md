# Scripts

EDSS 다운로드, 압축 해제, 원본 목록 작성, 표준화, 품질검사 도구를 이 폴더에 둡니다.

- `build_edss_dataset.py`: 직접 CSV ZIP, 전체연도 단일 CSV, 중첩 ZIP을 스트리밍으로 읽어 원본 문자열과 행 추적정보를 보존한 주제 패널을 만든다.
- `validate_edss_dataset.py`: 결과를 처음부터 다시 읽어 행 수, 체크섬, 연도 범위, 식별자 결측과 `0101` 기준 학교연도 연결률을 검산한다.
- `inspect_edss_zip.py`: 개별 EDSS ZIP의 헤더·연도·행 수·체크섬을 조사한다.
- `download_edss.py`: 공식 목록과 내부 파일 식별자를 확인한다. 브라우저 팝업 문맥이 필요한 실제 ZIP은 이 스크립트의 독립 HTTP 요청으로 받을 수 없다.
