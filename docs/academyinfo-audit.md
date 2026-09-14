# 대학알리미 API 103개 기능 및 수집 규칙 검증

검토일: 2026-09-14 (Asia/Seoul)

## 결론

과거 실험의 응답 원본과 manifest를 모두 삭제한 뒤 통합 검증 노트북을 한 번
실행했다. 설정에 고정한 9개 서비스·103개 기능을 대표 범위에서 1행씩 실시간
요청했고, 103개 모두 HTTP·XML·`resultCode`·`totalCount` 검증을 통과했다.
100개 기능은 1건 이상, 3개 기능은 정상 0건이었다.

실행은 2026-09-14 14:30 KST에 시작해 약 11초 걸렸다. 이 검증은 2025년,
제주대학교, 학교구분 01 등 기능별 대표 범위 하나에 대한 가용성 검사다. 전체
데이터를 내려받지 않았으므로 모든 학교·모든 연도의 내용 완전성을 뜻하지 않는다.

## 확인된 수집 규칙

| 항목 | 확인 결과 | 운영 규칙 |
|---|---|---|
| 설정 범위 | 9개 서비스, 103개 기능 | 데이터 ID와 `(service, operation)`을 고정한다. |
| 학교별 기능 | 46개 | `학교코드 × 조사연도 × 기능` 단위로 요청한다. |
| 다중 학교코드 | 대표 6개 기능에서 미지원 | 한 요청에 학교코드 하나만 보낸다. |
| 조사연도 생략 | 대표 9개 기능에서 0건 | 연도가 필요한 기능에는 `svyYr`을 명시한다. |
| 103개 기능 live probe | 103/103 정상 | 먼저 1행으로 현재 가용성과 총건수를 확인한다. |
| 페이지 분할 | 학과정보 500행 경계에서 동일 행 13건 | 일반 fallback으로 사용하지 않는다. |
| 동시성 | 동시성 4 대표 요청 8/8 성공 | 기본 2, 검증 상한 4로 둔다. |

## 중요한 구분

`config/academyinfo/endpoints.yaml`의 `scope_strategy`는 전국·학교별·지역별처럼
요청 범위를 어떻게 나누는지만 정의한다. 단일 응답 또는 페이지 분할 같은 전송
방식은 `config/academyinfo/runtime.yaml`에서 별도로 정의한다.

Swagger가 필수로 표시한 입력값은 `swagger_required_parameters`로 기록한다.
실제 서버에서 필터 생략이 가능하다는 실험 결과와 공식 명세의 필수 표기를 같은
개념으로 취급하지 않는다.

## 품질상 주의점

- `SchoolMajorInfoService/getSchoolMajorInfo`의 2025년 현재 `totalCount`는
  60,919행이다.
- 500행씩 요청한 1·2페이지 사이에 완전 동일한 행 13건이 겹쳤다. 따라서
  페이지별 결과를 단순 결합하거나 중복 제거해 완전성을 주장할 수 없다.
- 단일 요청의 총건수가 검증 상한 70,000행을 넘으면 자동 페이지 분할하지 않고
  중단 후 기능별로 다시 검토한다.
- 이번 실행은 전량 응답이나 원천 중복을 다시 검사하지 않았다. 검증 결과는
  103개 기능의 대표 범위 가용성과 요청 규칙에 한정한다.

## 근거 파일

- 통합 검증 노트북: `notebooks/academyinfo_validation.ipynb`
- 공통 API 모듈: `src/academyinfo/client.py`
- 공통 검증 흐름: `src/academyinfo/validation.py`
- 명령행 진입점: `scripts/validate_academyinfo.py`
- 공통 모듈 단위검사: `tests/test_academyinfo_client.py`
- 검증 흐름 단위검사: `tests/test_academyinfo_validation.py`
- 기능·요청 범위 설정: `config/academyinfo/endpoints.yaml`
- 실행 정책: `config/academyinfo/runtime.yaml`

과거 실험의 응답 원본과 manifest는 통합 검증 완료 후 중간 산출물로 삭제했다.
현재 검증은 `src/academyinfo/validation.py`의 공통 `run_validation()`으로 수행한다.
CLI는 종료 코드와 요약을 제공하고, 노트북은 같은 반환값을 표와 차트로 표현한다.
두 실행 경로 모두 응답 원본이나 manifest를 만들지 않는다.

명령행에서는 다음과 같이 같은 검증 흐름을 실행한다.

```bash
uv run --with pyyaml python scripts/validate_academyinfo.py
```

성공하면 종료 코드 0, 설정·전송 오류는 1, 검증 규칙 불일치는 2를 반환한다.
마지막 확인에서 CLI와 노트북은 모두 103/103 기능 통과, 데이터 존재 100개,
정상 0건 3개, 학과정보 페이지 경계 중복 13건으로 일치했다.
