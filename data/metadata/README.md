# 수집 메타데이터

이 디렉터리는 Git에서 제외한 원본 파일을 검증하고 다시 수집할 수 있도록
출처, 요청 범위, 로컬 경로, 파일 크기와 SHA-256을 보존한다. 인증키, 쿠키,
KESS 소속·이용목적 값은 기록하지 않는다.

## 현재 매니페스트

| 파일 | 단위 | 현재 기록 |
|---|---|---:|
| `academyinfo_manifest.jsonl` | 대학알리미 API 수집 작업 | 36,938 |
| `edss_manifest.jsonl` | EDSS 물리 다운로드 단위 | 265 |
| `kess_manifest.jsonl` | KESS 연도별 XLSX | 146 |

`edss_attempts.jsonl`에는 첫 전체 실행 중 일시적 DNS 오류가 발생한 5건을
남긴다. 다섯 건은 선택 재실행으로 모두 수집됐으며 성공 상태는
`edss_manifest.jsonl`에서 확인한다. KESS 수집 실패는 없어서
`kess_attempts.jsonl`이 생성되지 않았다.

원본은 각각 `data/raw/academyinfo/`, `data/raw/edss/`, `data/raw/kess/`에
있으며 `.gitignore`로 제외한다. 매니페스트의 성공 기록만으로 완료를 판단하지
말고, 기록된 경로의 파일 존재 여부, 크기와 SHA-256을 함께 검증한다.
