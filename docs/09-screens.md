# 09. 화면

시각 기준은 [design.md](../design.md)다. 다섯 화면이 같은 내비게이션, 같은 서체, 같은 색을 쓴다.

| 화면 | 파일 | 하는 일 |
|---|---|---|
| 검색 | [web/index.html](../web/index.html) | 키워드와 기간. 서버가 켜져 있으면 수집 후 리포트로 이동 |
| 수집 중 | [web/progress.html](../web/progress.html) | 검색 제출 뒤의 상태. 인용을 확인하기 전에는 문장을 채우지 않는다 |
| 리포트 | [web/report.html](../web/report.html) | 쟁점, 썸네일 영상 카드, 기사. 예시 데이터 |
| 기록 | [web/history.html](../web/history.html) | 지난 리포트. 서버에서는 저장된 것만 |
| 결과 없음 | [web/empty.html](../web/empty.html) | 확인된 인용이 없을 때의 화면 |

실행 결과는 `python -m radar brief`가 만드는 HTML이다. 예시 리포트와 같은 클래스와 서체를 쓴다.

```bash
python -m radar doctor
python -m radar brief "미국 국채 금리" --days 14
python -m radar serve
```

키가 없으면 그 소스만 건너뛴다. 인용문이 원문에 없으면 그 문장은 리포트에 넣지 않는다.
