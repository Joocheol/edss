# 재사용 코드

`edss/`는 수집·정제·검증·분석 함수를 제공하는 Python 패키지입니다.
노트북과 실행 스크립트는 이 패키지의 같은 함수를 사용합니다.

```python
from edss import build_edss_school_year_bridge
```

저장소 루트에서 `python3 -m pip install -e '.[analysis]'`로 설치합니다.
기존 모듈 이름을 유지했고, CLI 인자 해석은 각 모듈의 `main()`에 있습니다.
`python3 scripts/<기존 파일명>.py` 명령도 사용할 수 있습니다.
