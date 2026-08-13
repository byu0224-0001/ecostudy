# 3층 경계 계약

이 문서는 study-os에서 **무엇을 코드에 굳히고 무엇을 굳히지 않는지**를 정한다.
경계가 흐려지면 국면이 바뀔 때마다 코드를 고쳐야 하고, 그 순간 이 저장소는
재사용 가능한 엔진이 아니라 일회성 스크립트 모음으로 되돌아간다.

## 왜 층을 나누는가

투자·매크로 분석에서 **어떤 변수가 중요한지는 국면마다 바뀐다.**
2021년에는 CPI였고, 2023년에는 SOFR-IORB였고, 2026년에는 JGB 30년물이다.
그래서 "중요한 변수 목록"은 코드에 넣을 수 있는 종류의 지식이 아니다.

반대로 **어떤 데이터를 어떻게 가져오는지, 변화율을 어떻게 계산하는지는 바뀌지 않는다.**
이 둘을 같은 파일에 두면 안 변하는 것을 고치다가 변하는 것을 망친다.

## Layer 1 — 결정론적 코드 (`core/`)

**판별 기준: 어떤 변수가 중요한지 모르는 상태에서도 만들 수 있는가?**

들어가는 것:

| 모듈 | 책임 |
|---|---|
| `core/sources/` | 소스별 fetch 어댑터. 임의의 종목코드·기간을 받아 가져온다 |
| `core/http.py` | 재시도, 백오프, 소스별 rate limit |
| `core/cache.py` | 원본 응답 그대로 보관 (감사 기록) |
| `core/series.py` | 출처가 붙어 다니는 데이터 캐리어 |
| `core/transform.py` | yoy, mom, annualized, zscore, percentile_rank, spread 등 산술 |

**금지 사항 — 이게 이 층의 유일한 규율이다.**

`core/`는 **어떤 시리즈가 중요한지 알아서는 안 된다.**

```python
# 금지: 국면 지식이 코드에 새어들었다
FED_HIKE_INDICATORS = ["CPI", "PCE", "PAYEMS", "JOLTS"]
KEY_YIELD = "10년 국고채"

# 허용: 임의의 행·필드를 뽑는 방법만 안다
krx.series("bond_kts", "CLSPRC_YD", start=..., end=...,
           match={"BND_EXP_TP_NM": "10", "ISU_NM": re.compile(r"^국고\d")})
```

두 번째 형태는 만기가 10년이든 30년이든, 국고채든 회사채든 같은 코드로
처리된다. 어떤 만기가 지금 중요한지는 `session.yaml`이 정한다.

ECOS 어댑터가 이 규율의 좋은 예다. 통계표 834종 중 무엇도 코드에 없고,
대신 **찾는 방법**(`tables`, `items`, `key_statistics`)이 들어 있다.
지표 목록을 상수로 박고 싶어질 때마다 그 목록이 6개월 뒤에도 맞을지 묻는다.
아니라면 `session.yaml`으로 내린다.

전자를 넣으면 다음 국면에서 `core/`를 고쳐야 한다.
Layer 1을 고쳐야 하는 상황이 생기면 그건 기능 요청이 아니라 **경계선 위반 신호**다.

## Layer 2 — 선언적 명세 (`sessions/*/session.yaml`)

**아직 구현 안 됨 — Step 2.** 계약만 먼저 고정한다.

이번 회차의 판단을 코드가 아니라 데이터로 적는다.

```yaml
regime_view: "장기 금리 상승이 기업 실적보다 먼저 밸류에이션을 누르는 국면"

series:
  - id: ktb_10y
    source: krx
    dataset: bond_kts
    field: CLSPRC_YD
    match: {BND_EXP_TP_NM: "10", GOVBND_ISU_TP_NM: 지표, ISU_NM: {regex: '^국고\d'}}
    transform: [level, {diff: {years: 1}}]

  - id: kospi
    source: krx
    dataset: index_kospi
    field: CLSPRC_IDX
    match: {IDX_NM: 코스피}
    transform: [level, {zscore: {window: 250}}]

  - id: cpi_yoy
    source: ecos
    dataset: 901Y009          # 소비자물가지수 — `cli.py ecos tables` 로 찾는다
    items: ["0"]              # 총지수 — `cli.py ecos items 901Y009` 로 찾는다
    cycle: M
    transform: [yoy]

thresholds:
  - {name: "커브 역전", series: ktb_10y_minus_3y, metric: level, below: 0}

frame:
  for:     "금리 부담이 실적 개선을 상쇄한다"
  against: "이익 성장률이 할인율 상승을 넘어선다"
```

`transform` 항목의 이름은 `core.transform.REGISTRY`의 키다.
`apply_named()`이 Layer 2에서 Layer 1로 넘어오는 유일한 통로다.

**국면이 바뀌면 이 파일만 바뀐다.** 다음 회차가 반도체면 시리즈 목록도
임계값도 프레임도 전부 다른 값이 들어가고 `core/`는 손대지 않는다.

## Layer 3 — AI 판단 (`agents/`)

**아직 구현 안 됨 — Step 4.**

| 역할 | 하는 일 | 제약 |
|---|---|---|
| Builder | 찬성 측 논리 구성 | Skeptic 결과를 보지 못함 |
| Skeptic | 반대 측 논리 구성 | Builder 결과를 보지 못함 |
| Verifier | 숫자를 캐시된 원본과 대조 | 해석 금지, 대조만 |
| Editor | 분량 조정, 논점 추출 | 새 주장 추가 금지 |

Builder와 Skeptic의 격리가 핵심이다. 한 컨텍스트에서 순차로 시키면 두 번째가
첫 번째에 반응해서 반드시 약해지고, 허수아비가 만들어진다.

AI 산출물은 **Layer 1의 검증기를 통과하고 사람이 승인**해야 회차에 들어간다.

## 층 간 계약

```
Layer 3 (AI)        제안한다.        확정하지 않는다.
    ↓ 사람 승인
Layer 2 (yaml)      선언한다.        계산하지 않는다.
    ↓ apply_named
Layer 1 (code)      실행한다.        판단하지 않는다.
```

역방향 의존은 없다. `core/`는 `sessions/`와 `agents/`의 존재를 모른다.

## 출처는 데이터에서 떨어지지 않는다

Layer 1이 Layer 3을 검증할 수 있는 근거는 `Provenance`다.
변환을 거쳐도 `source`, `citation`, `cache_keys`가 따라오고 `lineage`에
계산 이력이 쌓인다.

```python
s = krx.close("005930", start="2025-01-01", end="2026-08-12")
out = transform.zscore(transform.yoy(s))

out.prov.lineage    # ['column(close)', 'yoy', 'zscore(expanding)']
out.prov.citation   # 'KRX 시세 (공공데이터포털 Open API)'
out.prov.cache_keys # 원본 응답을 되짚을 수 있는 키
```

두 시리즈를 결합해도 양쪽 출처가 합쳐진다.

```python
sp = transform.spread(a, b)
sp.prov.citation    # 'KRX 시세 ...; 금융감독원 전자공시시스템(DART) ...'
```

이 덕분에 차트 각주, 브리핑 출처, Verifier가 **같은 하나의 기록**을 읽는다.

## 캐시는 성능 장치가 아니라 감사 기록이다

`cache/`에는 공식 API가 반환한 바이트가 그대로 들어간다. 목적은 두 가지다.

1. **재현성** — 과거 회차의 숫자를 네트워크 없이 다시 계산할 수 있다
2. **검증** — AI가 쓴 숫자를 원본과 기계적으로 대조할 수 있다

그래서 캐시에는 **비밀키가 절대 들어가면 안 된다.**
`config.redact()`가 `key`, `token`, `secret`, `pw`, `crtfc`를 포함하는
파라미터를 저장 전에 치환하고, URL은 쿼리스트링을 잘라서 기록한다.

## 새 소스를 추가하는 방법

1. `core/sources/<name>.py`에 `Source` 서브클래스를 만든다
2. `name`, `citation`, `min_interval`을 정한다
3. `Table` 또는 `Series`를 반환하는 메서드를 만든다. `Provenance`를 반드시 채운다
4. `core/sources/__init__.py`의 `REGISTRY`에 한 줄 추가한다

다른 파일은 고치지 않는다. 고쳐야 한다면 추상화가 잘못된 것이다.
