# EDSS

EDSS 개방데이터를 이용해 2009~2025년 한국 고등교육 장기 패널을 구축하는 프로젝트입니다.

## 기본 전략

- 역사 패널의 주 자료원: EDSS 개방데이터
- 최신 연도 갱신·교차검증: 대학알리미 Open API
- 분석 단위: 대학·캠퍼스·대학원·학과·연도
- 핵심 분야: 학생, 교원·연구, 재정, 교육여건, 산학협력, 취업
- 학교코드와 학과코드는 숫자가 아닌 문자열 식별자로 보존

## 폴더 구조

```text
config/          수집대상과 우선순위
data/raw/        내려받은 원본 파일(Git 제외)
data/processed/  정제·결합 데이터(Git 제외)
data/metadata/   수집기록, 스키마, 품질검사 결과
docs/reference/  제공목록과 원자료 설명서
notebooks/       탐색·검증 노트북
scripts/         다운로드·정제·검증 도구
tests/           파싱·페이지네이션·중복·오류 응답 테스트
logs/            인증정보가 제거된 로컬 실행 로그(Git 제외)
```

## 원칙

1. 원본 파일은 수정하지 않고 그대로 보존합니다.
2. 모든 다운로드에는 출처, 테이블명, 대상 연도, 수집시각, 체크섬을 기록합니다.
3. 학교·캠퍼스·학과 식별자의 연도별 변경을 별도 매핑표로 관리합니다.
4. 집계값은 원자료에서 재현 가능하도록 계산 정의를 기록합니다.
5. 인증정보와 개인식별정보는 저장소에 커밋하지 않습니다.

## 출처

- EDSS: https://www.edmgr.kr/edss/
- EDSS 개방데이터: https://www.edmgr.kr/edss/es/opd/odd/od/es_opd_oddod01_001
- 대학알리미 Open API: https://www.data.go.kr/

## 빠른 시작

Python 3.11 이상과 표준 라이브러리만 사용합니다. 명령은 저장소 루트에서 실행합니다.

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

공공데이터포털의 직접 다운로드형 파일을 수집합니다. 정상 파일과 SHA-256이 이미 기록돼 있으면 다시 받지 않습니다.

```bash
python3 scripts/download_files.py \
  --config config/file_datasets.json \
  --raw-root data/raw/public_data_portal \
  --manifest data/metadata/file_manifest.jsonl \
  --log logs/file_collection.log
```

EDSS 우선순위 1의 연도별 파일 목록만 검증하거나 전체연도 묶음 파일을 수집합니다.

```bash
python3 scripts/download_edss.py --list-only --priority 1
python3 scripts/download_edss.py --priority 1 --year ALL
```

EDSS 웹 화면의 다운로드 팝업에서 받은 ZIP은 원본을 `data/raw/edss/`에 복사한 뒤, 압축을 풀지 않고 스키마와 체크섬을 등록할 수 있습니다. 목록 행의 `다운로드`를 누른 다음 팝업의 `전체 다운로드`를 선택해야 하며, 화면에 보이는 내부 주소를 독립 HTTP 요청으로 호출하는 방식은 동작하지 않습니다.

```bash
python3 scripts/inspect_edss_zip.py data/raw/edss/고등교육통계/0101_고등교육학교개황/0101_고등교육학교개황_2009-2025.zip \
  --output data/metadata/edss_0101_schema.json \
  --manifest data/metadata/edss_file_manifest.jsonl \
  --dataset 고등교육학교개황 \
  --catalog-code 0101 \
  --domn-code 16918 \
  --source-url https://www.edmgr.kr/edss/es/opd/odd/od/es_opd_oddod01_001
```

현재 EDSS 웹 브라우저의 전체 다운로드는 성공하지만 동일한 내부 주소를 독립 HTTP 수집기로 호출하면 404가 발생합니다. 이는 파일 부재가 아니라 브라우저 세션 또는 팝업의 선택 문맥 차이로 분류합니다. 현재 `0101 고등교육학교개황`, `0103 대학교학생개황`, `0105 대학원학생개황`의 2009~2025 전체연도 ZIP을 확보했습니다.

## 대학알리미 Open API

공공데이터포털의 일반 인증키를 로컬 `.env`에 설정합니다. 키는 채팅, 문서, 로그, 커밋에 넣지 않습니다.

```bash
cp .env.example .env
# .env를 직접 열어 DATA_GO_KR_SERVICE_KEY 값을 입력
```

Encoding/Decoding 키는 기본 `auto` 모드가 안전하게 판별합니다. API 요청 URL과 키 값은 로그에 남지 않습니다. StudentService의 연세대학교(학교 ID `0000149`) 2008·2009·최신 지원 연도를 비교합니다.

```bash
python3 scripts/collect_api.py \
  --service student \
  --operation /getComparisonEnrolledStudentCrntSt \
  --school-id 0000149 \
  --years 2008 2009 latest \
  --num-rows 999
```

중단 후 재실행하면 정상 XML 페이지는 체크한 뒤 재사용합니다. 페이지네이션, 재시도, 호출 간격, 최대 요청 수(`--max-requests`)를 적용하며 중복 item과 오류 XML을 거부합니다. 인증키 없이 공식 응답 필드 사전만 다시 만들 수 있습니다.

```bash
python3 scripts/collect_api.py --schema-only \
  --field-dictionary data/metadata/student_field_dictionary.csv
```

## 인벤토리와 메타데이터

- `data/metadata/edss_catalog_inventory.csv`: 기준 Excel 7개 시트의 600개 행
- `data/metadata/source_inventory.csv`: EDSS 우선순위 항목, 포털 파일데이터 12개, Open API 9개
- `data/metadata/edss_field_dictionary.csv`: EDSS 공식 목록의 원본 항목명 10,505개
- `data/metadata/student_field_dictionary.csv`: StudentService 공식 응답 필드
- `data/metadata/file_manifest.jsonl`: 파일명, 출처, 크기, SHA-256, 기준연도, 라이선스
- `data/metadata/edss_download_attempts.jsonl`: 연도 목록과 다운로드 성공·실패 이력
- `data/metadata/edss_file_manifest.jsonl`: EDSS 원본 ZIP의 체크섬과 수집 방식
- `data/metadata/edss_0101_schema.json`: 0101 ZIP의 연도별 행 수·인코딩·원본 헤더
- `data/metadata/edss_0103_schema.json`: 0103 ZIP의 연도별 행 수·인코딩·원본 헤더
- `data/metadata/edss_0105_schema.json`: 0105 ZIP의 연도별 행 수·인코딩·원본 헤더

원본과 정제 데이터는 재배포 조건과 크기를 확인할 때까지 Git에서 제외합니다. 체크섬과 위 실행 명령으로 재수집할 수 있습니다. 최초 수집 결과와 현재 차단 사항은 `docs/initial_collection_report.md`에 기록합니다.
