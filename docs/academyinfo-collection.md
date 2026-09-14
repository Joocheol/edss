# 대학알리미 API 수집 프로그램

검증한 9개 서비스의 103개 기능을 실제 XML 원본으로 수집하는 실행 진입점은
`scripts/collect_academyinfo.py`다. 재사용 코드는 `src/academyinfo/`에 둔다.
구성요소의 책임, 검증 계약, 저장 불변조건과 단계별 완료 기준은
[대학알리미 API 수집기 설계 및 이행 계획](academyinfo-collector-design.md)에 정리한다.

## 수집 흐름

1. 요청 연도별 `getUniversityCode` 전체 응답에서 학교코드를 확정하고, 각 행의
   학교코드·연도·중복 여부를 검사한다.
2. `config/academyinfo/endpoints.yaml`의 범위 전략으로 수집 작업을 만든다.
3. 각 작업을 `numOfRows=1`로 먼저 조회하여 `totalCount`를 확인한다.
4. 1행을 넘으면 검증된 `totalCount`를 `numOfRows`로 지정해 한 번에 받는다.
5. HTTP 상태, `resultCode`, `totalCount`, 실제 `item` 수와 설정의 출력 필드를 검사한다.
   값이 없는 선택적 필드는 XML 태그가 생략될 수 있으므로 허용하되, 설정에 선언되지 않은
   필드가 나타나면 실패한다. 완전히 같은 행의 중복 수는 원문 문자열 그대로 계산하되
   원본에서 제거하지 않는다.
6. XML 원본을 덮어쓰지 않고 저장하고 SHA-256과 수집 메타데이터를 manifest에 기록한다.

전체 응답이 70,000행을 넘으면 자동 페이지 분할을 시도하지 않고 중단한다. 현재 가장
큰 것으로 확인한 `getSchoolMajorInfo` 60,919행은 이 범위 안에 있다. 동시 요청 기본값은
2개, 허용 상한은 4개다.

## 실행

실제 다운로드에는 `.env`의 `ACADEMYINFO_SERVICE_KEY`가 필요하다. 읽기 전용
`--plan-only`에는 인증키가 필요하지 않다. 연도는 사고 방지를 위해 반드시 명시한다.

```bash
uv run --with pyyaml python scripts/collect_academyinfo.py --year 2025
```

특정 기능이나 학교만 작게 실행할 수 있다.

```bash
uv run --with pyyaml python scripts/collect_academyinfo.py \
  --year 2025 \
  --operation getUniversityCode

uv run --with pyyaml python scripts/collect_academyinfo.py \
  --year 2025 \
  --operation getComparisonEnrolledStudent \
  --school-id 0000027
```

여러 연도·기능·학교는 같은 옵션을 반복한다. `--limit N`은 학교목록 확인 이후
정렬된 본 작업의 앞 N개만 실행하는 스모크 테스트용이다. 학교목록이 아직 없으면
연도마다 `getUniversityCode`에 최대 2번의 요청이 별도로 필요하다. `--concurrency`는
4를 넘길 수 없다.

작업 수만 확인할 때는 `--plan-only`를 쓴다. 이 옵션은 기본적으로 네트워크 요청이나
파일 쓰기를 하지 않는다. 학교별 기능에는 `--school-id` 또는 이미 저장된 해당 연도의
`getUniversityCode` 응답이 필요하다. 계획 단계에서 누락된 학교목록을 명시적으로
받으려면 `--discover-schools`를 함께 지정한다.

장시간 수집은 로그를 저장하며 백그라운드에서 실행할 수 있다.

```bash
nohup uv run --with pyyaml python scripts/collect_academyinfo.py \
  --year 2025 > logs/academyinfo-2025.log 2>&1 &
```

## 저장과 재시작

- 원본: `data/raw/academyinfo/{service}/{operation}/{task_id}.xml`
- 수집기록: `data/metadata/academyinfo_manifest.jsonl`

`task_id`는 서비스, 기능, URL, 범위 전략, 비밀이 아닌 요청 파라미터로 결정한다.
manifest에는 데이터 ID, 서비스, 기능, 쿼리 없는 출처 URL, 범위 전략, 요청 파라미터,
수집시각, HTTP 상태, `resultCode`, 행·열 수, 실제 필드명, 완전 동일 중복행 수, 바이트
수와 SHA-256을 남긴다. 인증키는 URL 구성 단계에서만 내부 주입하며 manifest와 오류
메시지에 넣지 않는다. 학교·캠퍼스·학과 식별자는 문자열로만 받아 앞자리 0을 보존한다.

103개 기능의 공식 출력 필드에는 기본키가 선언되어 있지 않고, 일부 실응답에는 완전히
동일한 행도 존재한다. 따라서 현 단계에서는 추정한 의미키의 유일성을 강제하지 않는다.
의미키는 전 연도·전 범위의 결측과 충돌을 측정해 기능별 `key_fields`가 확정된 뒤 추가한다.

같은 명령을 다시 실행하면 manifest의 경로·파일 크기·SHA-256을 먼저 검증한다. 정상인
완료 작업은 HTTP 요청 전에 건너뛴다. manifest는 최초 한 번 인덱싱한 뒤 추가된 줄만
읽는다. 원본 저장 직후 강제 종료되어 manifest가 없는 경우에는 재다운로드한 파일의
크기와 SHA-256이 같을 때만 기록을 복구한다. 마지막 JSONL 줄이 일부만 쓰인 경우도
마지막 정상 위치로 복구한다. 동일 작업의 응답이 달라졌거나 원본이 변조·누락된 경우에는
기존 파일을 덮어쓰지 않고 명시적으로 실패한다.

## 2025년 작업 규모

실제 `getUniversityCode` 응답의 377개 학교를 기준으로 103개 기능을 모두 계획하면
17,431개 작업이다.

| 범위 전략 | 작업 수 |
|---|---:|
| 학교별·연도별 | 17,342 |
| 지역별·학교구분별 | 64 |
| 전국·연도별 | 5 |
| 코드 조회·연도별 | 4 |
| 1회 코드 조회 | 15 |
| 모든 학교구분 지역통계 | 1 |

학교 수가 달라지는 연도에는 전체 작업 수도 달라진다.

## 검증 결과

단위검사는 계획, 스트리밍 다운로드, 재시도, XML 형상·중복 검사, 원자 저장,
무덮어쓰기, 중단 복구와 manifest 재개를 포함한다. 최종 실데이터 스모크 테스트에서
2025년 `getUniversityCode`는 377행·14열, 제주대
`getComparisonFullTimeFacultyResearchCrntSt`는 1행·8열이었다. 두 응답 모두 완전
동일 중복행은 0개였고, 원본 크기와 SHA-256, XML `item` 수가 manifest와 일치했다.
같은 대학코드 명령의 두 번째 실행은 HTTP 요청 0건으로 완료 작업을 건너뛰었다.

2026-09-14에 2025년 전체 수집을 완료했다. 최종 수정 후 재실행 결과는 계획 17,431개,
완료 17,431개, 신규 저장 5,803개, 기존 완료 건너뛰기 11,628개, 실패 0개였다.
학교코드 집합은 377개이며 완료 manifest도 17,431개 기록을 포함한다.
