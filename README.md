# EDSS

EDSS 개방데이터를 이용해 2009~2025년 한국 고등교육 장기 패널을 구축하는 프로젝트입니다. 현재 우선순위 설정의 16개 물리 묶음으로 15개 주제 패널, 13,640,746행을 구축했습니다. 필수 세 분야의 전체 수집은 233개 논리 테이블·265개 물리 단위·278개 ZIP까지 완료했으며, 전체 재구성 입력은 `data/metadata/edss_full_rebuild_inventory.csv`에서 관리합니다.

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

현재 EDSS 웹 브라우저의 전체 다운로드는 성공하지만 동일한 내부 주소를 독립 HTTP 수집기로 호출하면 404가 발생합니다. 이는 파일 부재가 아니라 브라우저 세션 또는 팝업의 선택 문맥 차이로 분류합니다. 취업통계 전체 묶음은 생성 시간이 길어 2010~2024를 연도별로 수집했고, 나머지 대상은 전체연도 ZIP으로 확보했습니다.

원본을 검사하고 15개 주제 패널을 생성한 뒤 독립 검증합니다.

```bash
python3 scripts/build_edss_dataset.py --inspect-only
python3 scripts/build_edss_dataset.py
python3 scripts/validate_edss_dataset.py
python3 scripts/diagnose_edss_orphan_keys.py
python3 scripts/build_edss_school_year_bridge.py
```

모든 원본 값은 문자열로 보존하며 출처 ZIP, 내부 파일, 원본 행 번호와 결정적 행 ID를 추가합니다. 취업통계는 민감 가능 열과 2023년 구조 전환 때문에 `data/processed/edss/restricted/`에 분리합니다. 다른 패널을 `0101`에 결합할 때는 원시 `0101` 대신 학교연도 유일키 기준표를 사용합니다. 데이터 구조와 결합 주의사항은 `docs/edss_panel_dataset.md`, 상세 grain 규칙은 `docs/edss_school_year_bridge.md`를 참고합니다.

## 대학알리미 Open API 사후 검증

공공데이터포털의 일반 인증키를 로컬 `.env`에 설정합니다. 키는 채팅, 문서, 로그, 커밋에 넣지 않습니다.

```bash
cp .env.example .env
# .env를 직접 열어 DATA_GO_KR_SERVICE_KEY 값을 입력
```

Encoding/Decoding 키는 기본 `auto` 모드가 안전하게 판별합니다. API 요청 URL과 키 값은 로그에 남지 않습니다. API는 EDSS 원자료를 대체하지 않고 학교 ID·학교명 교차표, 최신 상태와 집계값의 사후 검증에 사용합니다. StudentService의 연세대학교(학교 ID `0000149`) 2008·2009·최신 지원 연도를 비교하는 명령은 다음과 같습니다.

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
- `data/metadata/edss_panel_catalog.csv`: 15개 논리 패널의 행·열·연도·출력 체크섬
- `data/metadata/edss_panel_data_dictionary.csv`: 원본 필드명, 저장형, 관찰 자료형, 단위·결측 정의 상태
- `data/metadata/edss_panel_quality_report.json`: 빌드 중 행·열·중복·식별자 검사
- `data/metadata/edss_panel_validation.json`: 전체 패널 독립 재검산과 학교연도 결합 범위
- `data/metadata/edss_school_year_bridge.csv`: 비어 있지 않은 학교연도 키마다 한 행인 안전 결합 기준표
- `data/metadata/edss_school_year_bridge_summary.json`: 입력 체크섬, 기준표 유일성, 데이터셋별 left join 무증식 검증
- `data/metadata/edss_full_rebuild_inventory.csv`: 전체 265개 물리 단위를 233개 논리 테이블로 묶은 재구성 입력과 원본 ZIP 추적정보
- `data/metadata/edss_full_rebuild_inventory_summary.json`: 현재 15개 패널과 전체 입력의 범위 차이, 분야별 수량, 파일·SHA-256 재검산 결과

원본과 정제 데이터는 재배포 조건과 크기를 확인할 때까지 Git에서 제외합니다. 체크섬과 위 실행 명령으로 재수집할 수 있습니다. 최초 수집 결과와 현재 차단 사항은 `docs/initial_collection_report.md`에 기록합니다.
