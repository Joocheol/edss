# 연구 프로젝트 구조

2026-09-13 「코덱스 Site 사용법 찾기」 대화에서 정한 구조를 EDSS에 적용했습니다.
작업 기준 저장소는 `/Users/joocheol/Documents/GitHub/edss`, 원격은
`https://github.com/Joocheol/edss.git`입니다.

```text
edss/
├── data/
│   ├── raw/           # 원본 보존
│   ├── interim/       # 중간 데이터
│   ├── processed/     # 분석 입력
│   └── metadata/      # 수집·스키마·검증 기록
├── notebooks/         # 탐색과 해석, src 함수 사용
├── src/edss/          # 공통 수집·정제·분석·검증 함수
├── scripts/           # 실행 진입점
├── outputs/
│   ├── results/
│   ├── figures/
│   ├── tables/
│   └── reports/
├── manuscript/chapters/
├── docs/findings/      # 연구자의 해석과 후속 계획
├── config/            # 기존 수집 설정
├── tests/             # 핵심 계산 검증
└── logs/              # 로컬 실행 로그
```

## 작업 흐름

1. 노트북에서 탐색하고 방법을 검토합니다.
2. 반복·확정된 기능은 `src/edss/`에 함수로 작성합니다.
3. `scripts/`는 해당 기능을 실행하며 기존 CLI 이름과 인자를 유지합니다.
4. 분석 입력은 `data/`, 보고할 결과는 `outputs/`에 저장합니다.
5. 해석·한계·판단은 `docs/findings/`, 출판할 글은 `manuscript/`에 씁니다.

## 환경과 검증

```bash
cd /Users/joocheol/Documents/GitHub/edss
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[analysis,notebooks]'
python -m unittest discover -s tests -p 'test_*.py'
python scripts/build_edss_dataset.py --help
```

노트북에서는 `from edss import 모듈명`으로 함수를 사용합니다.
저장소의 CLI와 테스트는 설치하지 않아도 로컬 `src/`를 찾습니다.
수집부터 전체 재구성까지의 기존 명령과 옵션은 루트 README와
`scripts/README.md`를 따릅니다. 장시간 수집·전체 재구성은 구조 개편 검증에서
실행하지 않습니다.

## 기존 작업 보존

원본·정제 데이터, 기존 메타데이터와 문서 경로는 변경하지 않았습니다.
이전 `Documents/ChatGPT/EDSS` 복사본에는 GitHub보다 오래된 감사 상태와
미반영 1209 재검증 제안이 함께 있습니다. 전체 복사본을 덮어쓰지 말고,
해당 제안은 별도 검토 후 반영합니다. 앞으로 코드 수정은 GitHub 폴더에서 합니다.
