# 정답지

라벨 붙이는 사람을 위한 문서. 왜 이걸 하는지는 `docs/EVALUATION_PLAN.md`.

```
dev/        지금 14통에서 뽑은 표본. 고치면서 계속 본다.
holdout/    앞으로 올 새 메일. 끝까지 안 본다.
```

```bash
python3 cli.py golden seed all --llm    # 표본 뽑기
python3 cli.py golden status            # 어디까지 채웠나
python3 cli.py golden score             # 채점
```

`seed` 를 다시 돌려도 이미 채운 라벨은 안 지운다.

## 순서

**claims → recall → pairs → topics.**

사건은 주장을 재료로 만들어진다. 재료가 성한지 모르는 채로 요리 맛을
평가하면, 맛이 없을 때 재료 탓인지 조리 탓인지 알 수 없다.

가장 급한 질문은 이것이다.

> 880건 중 실제로 필요한 것은 몇 개인가?

`claims` 의 `utility` 만 채워도 여기 답이 나온다. 나머지는 그다음이다.

## 네 가지 규칙

**추측(`heuristic`)을 보고 따라 쓰지 마라.** 그건 답이 아니라 우리가
맞히려는 대상이다. 따라 쓰면 정확도 100%가 나오고 아무것도 알 수 없게 된다.
읽지 않고 판단한 뒤 마지막에 비교하는 편이 낫다.

**확신이 없으면 비워 둬라.** 빈 줄은 채점에서 빠진다. 억지로 채운 애매한
판정 하나가 자를 휘게 만든다. 대신 `note` 에 왜 애매한지 적어 둔다.

**모델에게 대신 채우게 하지 마라.** 표본을 고르는 데는 코드도 모델도
써도 된다. 정답은 사람만 쓴다. 모델이 붙인 라벨로 그 모델을 채점하면
자기 답안지로 자기를 채점하는 셈이다. `seed` 는 저장할 때 답안 칸을 한 번
더 비우지만, 지키는 것은 결국 사람이다.

**holdout 은 열지 마라.** 한 번 보면 그 순간 개발용이 된다. 되돌릴 수 없다.

---

## blocks.jsonl — 블록 분류

```json
{"id": "DAILY_BYTE:2026-08-13:b4", "heuristic": "brief_news",
 "text": "...", "label": null, "note": ""}
```

`label` 에 하나:

| 값 | 뜻 |
| --- | --- |
| `lead_story` | 그날의 주 기사 |
| `brief_news` | 짧은 소식 묶음 |
| `calendar` | 앞으로 있을 일 |
| `sponsored` | 돈 받고 실은 것. 내용은 있다 |
| `self_promo` | 자기 서비스 홍보 |
| `lifestyle` | 투자와 무관한 읽을거리 |
| `footer` | 발행 정보, 수신거부 |
| `ui_residue` | "답변 보기" 같은 버튼 조각 |

`sponsored` 와 `self_promo` 를 가르는 기준은 **누구 얘기냐**다. 남의 상품을
소개하면 `sponsored`, 이 뉴스레터 자기 얘기면 `self_promo`. 제목에 `(광고)`가
붙었는지는 보지 마라. 전부 붙어 있다.

`ui_residue` 는 읽을 내용이 없는 조각이다. 짧다고 다 여기 넣으면 안 된다.
숫자 하나짜리 짧은 소식은 `brief_news` 다.

---

## claims.jsonl — 주장 (여기부터)

```json
{"id": "...", "claim": "코스닥이 6.97% 올라 854.47로 마감했다",
 "span": "코스닥 지수가 6.97% 오른 854.47에 마감하며",
 "heuristic": "numeric_fact", "heuristic_mode": "direct_data",
 "provenance": "exact",
 "utility": null, "label": null, "label_mode": null,
 "atomic": null, "duplicate_of": null, "note": ""}
```

### utility — 남길까 버릴까 (제일 중요)

**맞는 주장과 필요한 주장은 다르다.** "코스닥이 7% 올랐다"는 유형도 맞고
원문에도 있다. 그런데 그 글의 요지가 "기관 수급과 정책 기대가 겹쳐
급등했다"라면, 이 주장 하나만으로는 리서치에 쓸 게 없다.

주장 추출의 목표는 모든 사실을 빠짐없이 옮기는 것이 아니라, **뒤에서
사건과 주제를 뽑는 데 필요한 것만 남기는 것**이다.

| 값 | 언제 |
| --- | --- |
| `keep_core` | 글의 요지를 이루는 주장. 이게 틀리면 결론이 바뀐다 |
| `keep_supporting` | 요지는 아니지만 근거로 필요하다 |
| `drop_background` | 맞는 말이지만 각주다. 회사 소개, 제도 설명 |
| `drop_redundant` | 다른 주장과 사실상 같다. `duplicate_of` 도 채운다 |
| `drop_invalid` | 잘못 뽑았거나 원문이 받쳐 주지 않는다 |

여기만 채워도 880 중 몇 개가 필요했는지 답이 나온다.

**캔버스로 붙이기:** [golden-calibration.canvas.tsx](/Users/byeong-uk-yu/.cursor/projects/Users-byeong-uk-yu-Desktop-study-os/canvases/golden-calibration.canvas.tsx) 를 채팅 옆에서 열고 20건을 먼저 본다. 끝나면 `python3 cli.py golden import-canvas`.

### 나머지 칸

`label` — 무엇에 관한 말인가. `numeric_fact` `event_fact`
`market_observation` `policy_fact` `corporate_fact` `causal_claim`
`interpretation` `forecast` `opinion` 중 하나.

`label_mode` — 어떻게 확인하나. `direct_data` `primary_source`
`multi_source_research` `interpretive` `future_tracking` `not_researchable`
중 하나. **확인할 가치가 있냐를 묻는 게 아니라 어느 경로로 가냐를 묻는다.**

`atomic` — 한 사실만 담았으면 `true`. "AI 투자가 늘어 메모리가 부족해졌다"는
인과 주장 하나이므로 `true`. 서로 상관없는 사실 둘이 한 문장에 있으면 `false`.

`provenance` 는 코드가 이미 채운 값이라 고칠 필요 없다. 다만 `exact` 인데
읽어 보니 원문과 다르면 `note` 에 적어 달라. 대조 코드가 틀린 것이다.

---

## recall.jsonl — 빠뜨린 주장

```json
{"id": "DAILY_BYTE:2026-08-13:b4", "text": "블록 원문 전체...",
 "extracted": ["뽑힌 주장 1", "뽑힌 주장 2"],
 "reviewed": false, "kept_count": null, "missed": [], "note": ""}
```

**주장마다 라벨을 붙이는 것으로는 빠진 것을 셀 수 없다.** 아예 안 뽑힌
주장에는 붙일 라벨이 없기 때문이다. 블록을 통째로 읽고 `missed` 에 적는다.

1. 블록 원문(`text`)을 읽는다.
2. `extracted` 중 실제로 필요했던 개수를 `kept_count` 에 적는다.
3. 뽑혔어야 하는데 빠진 주장을 `missed` 에 문장으로 적는다.
4. `reviewed` 를 `true` 로 바꾼다.

---

## cluster.jsonl — 좁은 구간 전수 (묶기 재현율)

```json
{"id": "...", "day": "2026-08-10", "source": "UPPITY", "claim": "...",
 "machine_event": "E-012", "event_group": null, "theme_group": null, "note": ""}
```

기계가 올린 쌍만 정답지에 넣으면 찾기 재현율은 정의상 100% 가 된다.
그래서 하루치 주장 30개를 골라 **사람이 더미로 나눈다.**

- `event_group` — 같은 사건이면 **같은 이름**을 적는다 (예: `코스닥급등`)
- `theme_group` — 사건은 다르지만 같은 줄기면 같은 이름

30개를 나누면 그 안의 435쌍 답이 전부 따라 나온다. `machine_event` 와
비교하면 기계가 같은 사건을 얼마나 찾았는지 재현율이 나온다.

---

## pairs.jsonl — 두 주장이 한 사건인가

```json
{"id": "C-001",
 "a_claim": "코스닥은 외국인·기관 매수에 힘입어 6.97% 급등했다",
 "b_claim": "기관은 코스닥을 1조 2,240억 원 순매수했다",
 "token_overlap": 0.28, "retrieved": true,
 "heuristic": "same_event", "label": null, "note": ""}
```

사건끼리가 아니라 **주장끼리** 짝지어져 있다. Claim Pair는 Event 자체가
아니라 Event clustering을 위한 **pairwise evidence**다.

| 값 | 언제 |
| --- | --- |
| `same_event` | 같은 일을 다룬다 |
| `related_theme` | 다른 일이지만 같은 줄기다 |
| `unrelated` | 남남이다 |

날짜를 꼭 보라. "코스피 6,813.34 상승"과 "코스피 6,579.04 상승"은 단어가
100% 겹치지만 **다른 날의 다른 마감**이다.

`retrieved` 는 기계가 이 둘을 실제로 한 사건에 넣었는지다. 고치지 마라.

---

## topics.jsonl — 주제 품질

```json
{"id": "E-003", "question": "...", "confidence": 0.72,
 "criteria": {"why_now": null, "investment_link": null,
              "debatable": null, "standalone": null,
              "evidence_ready": null},
 "label": null, "note": ""}
```

`criteria` 다섯 개를 각각 `true`/`false` 로.

| 기준 | 묻는 것 |
| --- | --- |
| `why_now` | 지난달에 해도 됐을 얘기 아닌가 |
| `investment_link` | 결국 무엇을 사고 팔지에 닿는가 |
| `debatable` | 답이 정해져 있으면 두 시간이 안 간다 |
| `standalone` | 앞뒤 회차 없이 그날 하루로 성립하는가 |
| `evidence_ready` | 확인할 자료에 실제로 닿을 수 있는가 |

`label` 은 종합 판정. `usable`(이번 주에 열 만하다) `weak`(고치면 쓴다)
`reject`(안 된다).

여기서 `reject` 가 많이 나와도 실패가 아니다. **후보 4개 중 1개만 쓸 만해도
매주 하나는 나온다는 뜻**이다. 정직하게 매겨라.
