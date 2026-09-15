# EDSS·KESS 원본 다운로드 프로그램

확인일: 2026-09-15 (Asia/Seoul)

2026-09-15 실제 수집을 완료했다. EDSS는 265개 파일 1,315,517,008바이트,
KESS는 146개 파일 949,819,059바이트다. 총 411개 파일의 존재, 크기,
SHA-256과 ZIP/XLSX 구조를 원본에서 다시 읽어 전수 검증했으며 불일치는 없다.
EDSS 첫 실행에서 일시적 DNS 오류가 발생한 5건은 선택 재실행으로 모두
회수했고, KESS는 146건 모두 첫 실행에서 성공했다.

두 프로그램은 공식 화면에서 현재 카탈로그를 다시 읽고, 설정에 고정한 범위와
건수가 일치할 때만 수집한다. 원본은 수정하지 않으며 성공한 파일마다 크기와
SHA-256을 JSONL 매니페스트에 기록한다. 재실행 시에는 매니페스트뿐 아니라
실제 파일의 크기와 체크섬도 다시 확인한 후 건너뛴다.

## EDSS

EDSS 수집 범위는 고등교육통계 133건, 대학정보공시 130건, 취업통계 2건으로
총 265개 물리 다운로드 단위다. 공식 다운로드 엔드포인트는 일반 HTTP
클라이언트에 404를 반환하고 공식 페이지의 브라우저 요청만 처리하므로, 실제
다운로드의 기본 전송 방식은 로컬 Chrome이다. 카탈로그와 파일 목록 확인은
의존성 없는 HTTP 요청으로 수행한다.

먼저 현재 범위와 다운로드 계획을 확인한다.

```bash
python3 scripts/download_edss.py --catalog-only
python3 scripts/download_edss.py --plan-only
```

전체 원본을 받는다. `uv`는 Playwright만 임시 환경에 설치하고, Playwright가
이미 설치된 환경에서는 `python3 scripts/download_edss.py`로 실행해도 된다.

```bash
UV_CACHE_DIR=/tmp/edss-uv-cache \
  uv run --with playwright python scripts/download_edss.py
```

범위를 좁힐 때는 공식 분야명 또는 `domnCd`를 반복 지정할 수 있다.

```bash
UV_CACHE_DIR=/tmp/edss-uv-cache \
  uv run --with playwright python scripts/download_edss.py \
  --source 고등교육통계 --domn-code 11595 --year ALL --limit 1
```

브라우저 창을 보려면 `--headed`를 더한다. `--transport http`는 현재 EDSS
서버에서 404가 재현되는 진단용 옵션이며 정규 수집에는 사용하지 않는다.

- 원본: `data/raw/edss/`
- 성공 매니페스트: `data/metadata/edss_manifest.jsonl`
- 실패 시도: `data/metadata/edss_attempts.jsonl`
- 로그: `logs/edss-download.log`

## KESS

KESS 범위는 고등교육통계 7개 시계열·104개 연도별 파일과 졸업자 취업통계
3개 시계열·42개 연도별 파일로 총 146개 XLSX다. 먼저 공식 화면의 현재
카탈로그가 설정과 일치하는지 확인한다.

```bash
python3 scripts/download_kess.py --catalog-only
```

KESS는 파일을 받기 전에 소속과 이용 목적을 공식 화면에 제출하도록 요구한다.
실제 값에 맞는 키를 명시해야 하며 프로그램은 이를 매니페스트나 실패 기록에
저장하지 않는다.

```bash
python3 scripts/download_kess.py \
  --affiliation education_staff \
  --purpose academic_research
```

소속 키는 `government`, `research_institute`, `local_government`,
`education_staff`, `education_office`, `student`, `private_organization`,
`other`다. 이용 목적 키는 `general_interest`, `academic_research`, `policy`,
`research_project`, `civil_request`, `media`, `other`다.

도메인·시계열·연도로 범위를 좁힐 수 있다. 오타나 서로 맞지 않아 0건이 되는
필터는 오류로 종료한다.

```bash
python3 scripts/download_kess.py \
  --affiliation education_staff --purpose academic_research \
  --domain 고등교육통계 --series school_spring --year 2026
```

- 원본: `data/raw/kess/`
- 성공 매니페스트: `data/metadata/kess_manifest.jsonl`
- 실패 시도: `data/metadata/kess_attempts.jsonl`
- 로그: `logs/kess-download.log`

## 안전장치

- 공식 카탈로그의 분야별·시계열별 건수가 바뀌면 다운로드 전에 실패한다.
- 응답이 HTML, 빈 파일, 손상된 ZIP 또는 올바르지 않은 XLSX이면 게시하지 않는다.
- 파일은 `.part`에 쓴 뒤 검증이 끝난 경우에만 최종 이름으로 옮긴다.
- 같은 경로에 내용이 다른 파일이 있으면 덮어쓰지 않는다.
- 성공 매니페스트에는 출처, 공식 식별자, 시각, 파일명, 크기와 SHA-256을 남긴다.
- 실패는 다른 독립 파일의 수집을 막지 않으며, 종료 코드는 실패가 하나라도 있으면 1이다.
