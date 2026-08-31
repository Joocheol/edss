# EDSS 최초 수집 결과

기준일: 2026-08-31 (Asia/Seoul)

## 요약

- 기준 Excel의 7개 시트와 600개 데이터 행을 전수 추출했다.
- EDSS 공식 개방데이터 화면에서 현재 제공되는 561개 물리 항목과 10,505개 원본 필드명을 확보했다.
- 공공데이터포털의 `한국대학교육협의회` 검색 결과를 상세 페이지 기준으로 확인했다: 파일데이터 12개, Open API 9개, 연계데이터 1개.
- 직접 다운로드형 10개와 대학알리미 첨부 1개, 총 11개 원본(113,711,726 bytes)을 확보했다. 각 파일의 출처, 크기, SHA-256, 기준연도, 라이선스를 `data/metadata/file_manifest.jsonl`에 기록했다.
- EDSS 우선순위 1의 14개 물리 데이터셋에서 연도별 파일 식별자와 `ALL` 파일 존재를 확인했다. 실제 다운로드는 공식 서버가 모든 대상에 HTTP 404를 반환해 실패했다.
- 인증정보가 제거된 Open API 수집기와 13개 단위 테스트를 완성했다. StudentService 실제 비교 호출은 현재 환경에 인증키가 없어 아직 실행하지 못했다.

## 기준 Excel 조사

| 시트 | 공식 표기 규모 | 추출 범위 |
|---|---:|---|
| 교육통계(12종,106테이블) | 106 테이블 | `A1:I110` |
| 학교정보공시(6종,77테이블) | 77 테이블 | `A1:I85` |
| 특수교육(3종,38테이블) | 38 테이블 | `A1:I42` |
| 고등교육통계(7종,102테이블) | 102 테이블 | `A1:I137` |
| 대학정보공시(13종,130테이블) | 130 테이블 | `A1:I133` |
| 취업(1종), 평생(2종) | 복수 영역 | `A1:I12` |
| 에듀파인(6종,65테이블) | 65 테이블 | `A1:I72` |

병합 셀의 상위 분류를 아래 행에 이어 붙여 데이터셋명, 제공방식, 제공 URL, 연도, 단위, 주요 필드, 우선순위, 다운로드 상태를 행 단위로 보존했다. Excel 내부 문장은 데이터로만 처리했다. 결과는 `data/metadata/edss_catalog_inventory.csv`에 있다.

## 공공데이터포털 파일데이터 12개

검색 결과는 기관 필터가 아니라 검색어 결과이므로 한국전문대학교육협의회, 교육부, 한국장학재단 자료도 포함한다. 상세 목록과 필드는 `config/file_datasets.json` 및 `data/metadata/source_inventory.csv`에 있다.

| 데이터 ID | 데이터셋 | 제공기관 | 형식 | 확인 결과 |
|---|---|---|---|---|
| 15014632 | 대학알리미 대학별 학과정보 | 한국대학교육협의회 | CSV | 다운로드 완료 |
| 15081503 | 대학알리미 데이터 다운로드 | 한국대학교육협의회 | 외부 제공화면 | 화면 확인, 이용자 구분·목적 입력 필요 |
| 3059853 | 대학입학 DB | 한국대학교육협의회 | ZIP(HWP 3개) | 다운로드 완료 |
| 15068978 | 대학 정보 | 한국전문대학교육협의회 | CSV | 다운로드 완료 |
| 15068974 | 신입생 입시통계현황 | 한국전문대학교육협의회 | CSV | 다운로드 완료 |
| 3077936 | 전문대학 입시관련 정보 | 한국전문대학교육협의회 | XLSX | 다운로드 완료 |
| 15068976 | 학과 정보현황 | 한국전문대학교육협의회 | CSV | 다운로드 완료 |
| 15068977 | 외국인 입시정보 | 한국전문대학교육협의회 | CSV | 다운로드 완료 |
| 15068975 | 신입생 모집정보 | 한국전문대학교육협의회 | CSV | 다운로드 완료 |
| 15139338 | 학교별 학부·학과(전공) 리스트 | 교육부 | XLSX | 대학알리미 첨부 다운로드 완료 |
| 15122460 | 대학원 계약학과 설치 운영 현황 | 교육부 | XLSX | 다운로드 완료 |
| 15073573 | 학자금 대출 현황(대학정보공시) | 한국장학재단 | CSV | 다운로드 완료 |

`15081503` 외부 화면은 현재 2024·2025·2026년 항목을 표시하며 한 번에 10개까지 선택할 수 있다. 다운로드 전에 이용자 구분과 사용 목적을 제출해야 하므로 임의의 신분·목적을 제출하지 않았다.

## 공공데이터포털 Open API 9개

모든 상세 페이지의 현재 Swagger 명세, 수정일, XML 형식, 기본 URL, 기능별 경로와 응답 필드를 저장했다.

| 데이터 ID | 서비스 | 기본 URL | 수정일 | 기능 수 |
|---|---|---|---|---:|
| 15158684 | StudentService | `https://apis.data.go.kr/B340014/StudentService` | 2026-05-14 | 27 |
| 15158963 | BasicInformationService_2 | `https://apis.data.go.kr/B340014/BasicInformationService_2` | 2026-05-26 | 10 |
| 15158678 | EducationResearchService | `https://apis.data.go.kr/B340014/EducationResearchService` | 2026-05-15 | 22 |
| 15158680 | FinancesService | `https://apis.data.go.kr/B340014/FinancesService` | 2026-05-14 | 10 |
| 15158666 | SchoolMajorInfoService | `https://apis.data.go.kr/B340014/SchoolMajorInfoService` | 2026-05-04 | 1 |
| 15158679 | EducationConditionService | `https://apis.data.go.kr/B340014/EducationConditionService` | 2026-05-15 | 12 |
| 15158955 | BasicInformationService_1 | `https://apis.data.go.kr/B340014/BasicInformationService_1` | 2026-05-04 | 13 |
| 15158626 | IndustryAcademicCooperationService | `https://apis.data.go.kr/B340014/IndustryAcademicCooperationService` | 2026-04-28 | 7 |
| 15158665 | SchoolInfoService | `https://apis.data.go.kr/B340014/SchoolInfoService` | 2026-04-29 | 1 |

StudentService 공식 명세에서 `/getComparisonEnrolledStudentCrntSt`의 요청 필드는 `serviceKey`, `pageNo`, `numOfRows`, `schlId`, `svyYr`이며 응답 item 필드는 `indctId`, `indctVal1`, `schlDivNm`, `schlEstbNm`, `schlId`, `schlKrnNm`, `svyYr`로 확인했다.

### StudentService 비교 시험 상태

예정한 요청은 연세대학교 `0000149`, `pageNo=1`, `numOfRows=999`, 2008·2009·최신 지원 연도다. 현재 환경에는 `DATA_GO_KR_SERVICE_KEY`가 없어 네트워크 호출 전 안전하게 종료됐다. 따라서 2008년의 `totalCount=0` 또는 item 부재를 자료 부재로 단정하지 않았다. 인증키가 설정되면 수집기가 다음을 함께 기록한다.

- `resultCode`, `resultMsg`, `totalCount`, 실제 item 수
- 원본 응답 필드명과 최대 3개 item의 실제 값
- 정상 빈 응답과 인증 오류의 구분
- 최신 자료가 발견될 때까지의 제한된 역순 연도 탐색
- 원본 XML, 페이지별 SHA-256, 재개·중복 검사 결과

## EDSS 우선순위 파일 수집 결과

우선순위 1의 14개 물리 코드 모두에서 공식 파일 목록 조회는 성공했다. 고등교육·대학정보공시 항목은 2009~2025의 17개 연도 파일과 `ALL`을 표시했다. 취업통계는 `13299`가 2010~2022, `13300`이 2023~2024 구간을 담당한다.

`/es_opd_oddod01_005` 다운로드 요청은 브라우저 UI와 재현 수집기 모두에서 14개 대상 전부 HTTP 404를 반환했다. 연도 목록 조회가 정상이고 인증 화면으로 이동하지 않았으므로 인증키 오류가 아니라 현재 공식 다운로드 엔드포인트 또는 서버 저장 파일 문제로 분류했다. 대상 코드, 광고 연도, 시각, HTTP 상태는 `data/metadata/edss_download_attempts.jsonl`에 남겼다. `scripts/download_edss.py`는 서버가 복구되면 정상 파일을 중복 없이 받을 수 있다.

## 재현성과 보안

- `.env`와 `.env.*`는 Git에서 제외하고 `.env.example`만 추적한다.
- 수집기는 Encoding/Decoding 키를 `auto` 모드에서 안전하게 판별한다.
- 키 값과 키를 포함한 요청 URL은 출력·로그·메타데이터에 남기지 않는다.
- API 페이지네이션, 재시도, 호출 간격, 요청 예산, XML 오류, 중복 item, 정상 캐시 재사용을 구현했다.
- 원본 파일은 `data/raw/`에서 수정하지 않고 Git에서 제외했다. 원본 재배포 조건이 명확하지 않은 자료와 대용량 파일도 커밋하지 않는다.
- 학교 ID, 학교명, 본교·분교, 캠퍼스, 학과·전공 식별자, 조사·공시연도, 학교·설립유형, 지역 및 원본 행 식별정보는 표준화 과정에서도 원본값을 별도 보존한다.
- EDSS 필드명은 의미·자료형·단위·결측 정의를 추측하지 않았다. 공식 항목명만 확정하고 나머지는 원본 파일 또는 설명서 확인 필요 상태로 표시했다.

## 검증 결과

`python3 -m unittest discover -s tests -p 'test_*.py' -v` 결과 13개 테스트가 모두 통과했다. 검사 범위는 XML 정상·빈·인증 오류, 페이지네이션 재개, 페이지 간 중복, Encoding/Decoding 키 후보, HTML 오인 다운로드, XLSX 서명, EDSS 파일명과 성공 기록 재사용이다.

## 다음 수집 대상과 필요한 조치

1. 사용자가 저장소 로컬 `.env`에 공공데이터포털 인증키를 설정하면 StudentService 2008·2009·최신 연도 비교를 즉시 실행한다.
2. EDSS 공식 다운로드 서버의 404를 재확인하고, 복구 시 우선순위 1 `ALL` 원본부터 수집한다.
3. `15081503` 추가 수집이 필요하면 사용자가 실제 이용자 구분과 제출할 사용 목적 문구를 정해야 한다.
4. GitHub CLI 재로그인 후 공개 `edss` 저장소를 생성·연결하고 로컬 커밋을 푸시한다.
