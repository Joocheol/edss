# 대학알리미 대학원명 API 교차검증

## 결론

[한국대학교육협의회 대학별 학과정보 API](https://www.data.go.kr/data/15158666/openapi.do)는 대학원 단위 학교명, 일반·전문·특수대학원 구분, 전공, 학위과정, 입학정원, 졸업자 수를 제공한다. 2025년 전체 60,919행 중 일반·전문·특수대학원은 22,185행이며 대학원명은 1,581개다.

이 자료로 취업통계 2023–2024년 미연결 대학원 2,435개 학교–연도 identity, 19,477행을 비교한 결과 2,286개(93.8809%)가 대학원명·학교종류·지역으로 하나의 2025년 대학원과 연결됐다. 이 중 2,281개는 학과명도 하나 이상 겹쳤다. 남은 149개 학교–연도, 고유 학교명 108개는 명칭 변경·폐지·통합 여부를 별도로 검토해야 한다.

## 연결 규칙

- 유니코드, 공백, 기호를 정규화한 대학원명을 비교한다.
- 일반·전문·특수대학원 구분과 시도가 같아야 한다.
- API 명칭 끝의 캠퍼스·지역 괄호는 보조 규칙에서만 제거한다.
- 같은 이름·종류·지역에 API 대학원이 하나일 때만 이름 후보로 기록한다.
- EDSS와 API의 학과명 집합 교집합과 Jaccard를 보조 근거로 기록한다.

이 연결은 공식 식별자 교차표가 아니다. `candidate_open_id`는 비워 두고 `canonical_open_id_imputed=false`를 유지한다.

## 데이터 품질 제약

- API는 현재 2025년 60,919행만 반환한다. 2009–2024년 요청은 `NORMAL SERVICE.`이지만 `totalCount=0`이었다.
- 응답에는 `schlNm` 대학원명이 있으나 대학원별 `schlId`나 EDSS `개방ID`는 없다.
- `eschlPscpNum`은 입학정원, `grdtNum`은 졸업자 수다. 재적학생 수가 아니다.
- 원천 전체에는 완전히 동일한 중복 행 284개가 있다. 원본·집계 CSV에는 그대로 보존하고 품질 보고서에 기록했다.
- 이름이 유일하게 연결된 2,286개 중 5개는 2023–2024년 EDSS 학과와 2025년 API 학과의 교집합이 0이다. 이름 변경 또는 학과 개편 검토가 필요하다.
- 기준연도가 다르므로 학과 집합의 불일치만으로 잘못된 연결이라고 단정하지 않는다.

## 결과

| 학교종류 | 유일 이름·맥락 연결 | 미연결 | 합계 |
|---|---:|---:|---:|
| 일반대학원 | 341 | 34 | 375 |
| 전문대학원 | 386 | 15 | 401 |
| 특수대학원 | 1,559 | 100 | 1,659 |
| 합계 | 2,286 | 149 | 2,435 |

## 재실행

```bash
python3 scripts/collect_academyinfo_school_major.py --years 2025
python3 scripts/analyze_academyinfo_graduate_name_coverage.py
uv run --offline --with nbconvert --with ipykernel python -m jupyter nbconvert \
  --execute --to notebook --inplace notebooks/academyinfo_graduate_name_coverage.ipynb
```

원본 XML과 집계 CSV는 `data/raw/open_api/academyinfo_school_major/`에 보존하며 Git에는 커밋하지 않는다. 페이지별 체크섬과 수집 상태는 `data/metadata/academyinfo_school_major_manifest.jsonl`, 품질 요약은 `data/metadata/academyinfo_school_major_collection.json`, EDSS 이름 후보는 `data/metadata/edss_academyinfo_graduate_name_candidates.csv`, 연결 요약은 `data/metadata/edss_academyinfo_graduate_name_match.json`에 기록한다.
