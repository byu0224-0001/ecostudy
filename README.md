# study-os

격주 투자 스터디를 운영하기 위한 리서치 엔진.

목적함수는 **준비 시간 단축이 아니라 참가자 산출물 생성률**이다.
자료를 더 빨리 만드는 것보다, 참가자가 자기 기준을 세우고 남기게 만드는 것이 목표다.

설계 원칙은 [`docs/LAYERS.md`](docs/LAYERS.md)에 있다. 코드를 고치기 전에 읽는다.
주제를 고르는 규칙은 [`docs/TOPICS.md`](docs/TOPICS.md)에 있다.

## 현재 상태

Step 1 (데이터 레이어)와 주제 후보 풀까지 구현됨.

| Step | 내용 | 상태 |
|---|---|---|
| 0 | study-pack — 포맷 계약, 산출물 5종 | 완료 (별도 Cursor 스킬) |
| **1** | **`core/sources` + `transform` + 캐시** | **완료** |
| **1.5** | **주제 포착·대조·후보 풀 + 선정 규칙** | **완료** |
| 2 | `session.yaml` → pack.json 자동 생성 | 예정 |
| 3 | Verifier (숫자 대조) | 예정 |
| 4 | Builder / Skeptic 병렬 | 예정 |
| 5 | Regime Note + 좌표 매트릭스 | 예정 |
| 6 | 참여 지표 수집 | 예정 |
| 7 | 브리핑 프레젠테이션 모드 | 예정 |

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env      # 키를 채운다
python3 cli.py doctor     # 설정 점검
```

`doctor`는 어떤 키가 없는지 이름으로 알려준다. 값은 출력하지 않는다.

## 사용

```bash
# 종목 종가 (시장 자동 판별)
python3 cli.py price 005930 --start 2026-06-01 --end 2026-08-12 --close-only --save

# 데이터셋 목록
python3 cli.py datasets

# 하루치 전체 (지수, 국채, ETF, 금, 옵션 …)
python3 cli.py snapshot index_kospi 2026-08-12
python3 cli.py snapshot bond_kts 2026-08-12

# 임의 데이터셋에서 시계열 추출. = 는 완전일치, ~ 는 정규식
python3 cli.py series index_kospi CLSPRC_IDX --match 'IDX_NM=코스피' \
    --start 2026-06-01 --end 2026-08-12 --save

python3 cli.py series bond_kts CLSPRC_YD --match 'BND_EXP_TP_NM=10' \
    --match 'GOVBND_ISU_TP_NM=지표' --match 'ISU_NM~^국고\d' \
    --start 2026-06-01 --end 2026-08-12 --unit '%' --save

# 공시 목록 (DART). types 는 pblntf_ty: A 정기 B 주요사항 C 발행 D 지분 E 기타
python3 cli.py disclosures 005930 --start 2026-01-01 --end 2026-08-12 --types A

# 주요계정 (DART)
python3 cli.py financials 005930 --year 2025 --report annual

# 저장된 series 에 변환 적용
python3 cli.py transform krx krx_close_005930 yoy --save
python3 cli.py transform krx krx_close_005930 annualized --kwargs '{"months": 3}'

# 주제 — 읽다가 걸린 것을 던져 넣는다
python3 cli.py topics capture \
  --headline "장기금리 급등, 재정 적자 우려 확산" \
  --claim   "국고 30년 금리 상승의 주된 원인은 국채 발행 증가다" \
  --source  "한국경제 2026-08-11" \
  --verify  "krx bond_kts 커브, 기재부 발행계획"

# 지표로 대조한 뒤 판정
python3 cli.py topics check N-001 --result contradicts --note "커브가 뒤쪽만 들렸다"
python3 cli.py topics inbox --pending

# 정식 후보로 승격 — 축 없이 부르면 축별 관점과 배치 권장을 먼저 보여준다
python3 cli.py topics promote N-001
python3 cli.py topics promote N-001 --axis 4 --judgment 리스크 \
  --question "내 포트폴리오에서 장기금리 4.7%를 못 견디는 자산은 무엇인가" \
  --split "재정 수급이다" --split "인플레 기대다" \
  --output "줄일 것과 유지할 것" --ready partial \
  --series "krx:bond_kts:CLSPRC_YD"

# 후보 풀
python3 cli.py topics list
python3 cli.py topics list --axis 4 --ready full
python3 cli.py topics show T-002
python3 cli.py topics validate

# 회차 이력 — 다음에 어느 축을 할지, 이미 다룬 것은 아닌지
python3 cli.py sessions balance
python3 cli.py sessions open
python3 cli.py sessions similar --topic T-002
python3 cli.py sessions show S-001

# 캐시 현황
python3 cli.py cache
```

`--refresh`는 캐시를 무시하고 재요청한다. `--save`는 `series/`에 CSV와
출처 사이드카(`.meta.json`)를 남긴다.

## 주제 선정

규칙은 [`docs/TOPICS.md`](docs/TOPICS.md), 포착함은 `topics/inbox.yaml`,
후보 풀은 `topics/backlog.yaml`이다.

**뉴스가 유입, 지표가 검증이다.** 뉴스 단독으로 고르면 가격에 후행하고
프레임에 갇히고 결국 서사를 전달하게 된다. 지표 단독으로 고르면 아무도
궁금해하지 않는 이상치가 나온다. 지표가 광범위해서 못 쓰겠다는 것은
출발점으로 쓸 때만 맞는 말이고, 검증 도구로 쓰면 범위를 뉴스가 정해준다.

포착할 때 적는 것은 기사 요약이 아니라 **기사가 하는 검증 가능한 주장
하나**다. `--verify`를 채울 수 없으면 그 자리에서 버린다.

대조 결과가 넷으로 갈린다. 일치하면 확인하는 자리가 되니 좋은 주제가
아니고, **어긋나면 가장 좋은 주제**이며, **관측은 맞는데 인과가 열려 있으면
두 번째로 좋다**. 확인이 안 되면 탈락이다. 뉴스의 주장은 대개 관측과 인과
두 겹인데 이견은 거의 항상 인과에 있다.

### 소스는 층으로 나눈다

소스 목록을 길게 만드는 것은 선택을 돕지 않는다. 아홉 군데에서 모으면
후보가 주당 오십 개가 되고 다시 "뭘 고르지"로 돌아온다. 그래서 소스를
어디에 있는가가 아니라 **무엇을 알려주는가**로 나눈다 — `topics/sources.yaml`.

| 층 | 알려주는 것 | 쓰는 곳 |
|---|---|---|
| 통념 | 지금 사람들이 무엇을 믿고 있나 | 검증할 주장 |
| 견해 | 논쟁의 양쪽 논거 | 찬반 |
| 1차 | 실제로 무슨 일이 있었나 | 검증 |

**유튜브는 뉴스 소스가 아니라 통념 소스다.** 슈카월드·삼프로·언더스탠딩은
사실을 전하는 곳이 아니라 서사가 만들어지는 곳인데, 스터디원들이 실제로
보는 것이 그 채널이다. 채널 제목 한 주치는 묻지 않고 얻는 참가자 관심사다.

```bash
python3 cli.py topics feed --layer 통념 --days 7
python3 cli.py topics sources
```

여러 채널에 걸쳐 반복된 말을 먼저 보여준다. 한 채널이 같은 말을 열 번
하는 것은 그 채널의 관심사고, 서로 다른 채널이 같은 주에 같은 말을 하면
그것이 이번 주 통념이다. 여기서 나오는 것은 후보가 아니라 **재료**다.

### 받은 뉴스레터도 같은 층으로 들어온다

```bash
python3 cli.py topics mail --dir ~/Downloads --claims
```

내려받은 `.eml` 에서 숫자가 붙은 문장만 뽑는다. 매일 오는 데일리바이트·
UPPITY·뉴닉·딥서치를 안 쓸 이유가 없지만, 잘 정리된 글일수록 그대로 믿기
쉬워서 파서는 **어떤 항목도 사실로 표시하지 않는다.** 전부 대조 대상이다.

여기서만 얻는 것이 둘 있다. 하나는 인과다 — 어댑터는 엔화가 하루에 2.2%
빠진 것을 알지만 그것이 미일 공동 개입 때문이라는 건 여기서만 온다. 다른
하나는 앞으로 있을 일이다. 실적 발표일과 공모주 청약은 어떤 어댑터로도
가져올 수 없다. 자세한 것은 [docs/TOPICS.md](docs/TOPICS.md) 참조.

`--llm` 을 붙이면 블록 분류와 주장 추출을 모델이 한다. 주장마다 원문 구절을
받아 두는데, **모델이 냈다고 원문에 있는 것은 아니라서** 코드가 블록 원문에서
직접 찾아 `exact / stitched / fuzzy / missing` 을 매긴다. `exact` 가 아니면
자료에 인용하지 않는다.

```bash
python3 cli.py topics mail --llm --events --cost
```

`--events` 는 주장을 사건으로 묶고, `--cost` 는 단계별 호출 수·토큰·비용을
찍는다. 캐시가 있으면 2초에 끝나지만 그건 오늘 안 낸 돈일 뿐이다. 매일 아침
오는 새 메일은 전부 실호출이라 **운영 비용은 cold 기준으로만 본다.**

### 얼마나 잘하고 있는지 재는 자

```bash
python3 cli.py golden audit --llm    # 사람 손 없이 되는 점검
python3 cli.py golden seed all --llm # 라벨 붙일 표본 뽑기
python3 cli.py golden score          # 채점
```

사건 10개, 후보 4개가 나왔다는 건 분량이지 품질이 아니다. 그 4개 중에
실제로 열고 싶은 모임이 있는지는 정답지 없이는 알 수 없고, 정답지 없이
모델을 Luna 로 올리면 "좋아 보인다" 말고 할 말이 없다.

지금 14통은 프롬프트를 고치는 내내 봐 온 자료라 여기서 잘 나오는 건 당연하다.
새 메일 5~10통은 `data/golden/holdout/` 에 넣고 **끝까지 열지 않는다.**
방법은 [`docs/EVALUATION_PLAN.md`](docs/EVALUATION_PLAN.md).

### 한 사건이 여섯 개의 다른 회차가 된다

`topics promote`를 축 없이 부르면 같은 주장이 축마다 어떤 회차가 되는지를
먼저 보여준다. "외국인이 대만으로 갔다"는 하나의 주장이지만 1축에서는 국면
판단, 3축에서는 기업 재무 확인, 4축에서는 내 포트폴리오 점검이 된다.

문장을 대신 써주지는 않는다. 질문·찬반·산출물은 인자로 요구한다 — 정렬
기준에 직접 걸리는 것이고 사람만 쓸 수 있다. 승격이 자동으로 하는 일은
**이미 확인한 근거를 옮기는 것** 하나다.

후보 풀은 6축 전체를 채워둔다. **좋은 주제만 고르면 자연히 거시로 쏠린다.**
축을 먼저 정하고 그 안에서 고르는 순서가 아니면 항상 1축이 이긴다.

## 회차 이력

규칙은 [`docs/SESSIONS.md`](docs/SESSIONS.md), 기록은 `sessions/S-###.yaml`이다.

**회차 기록에도 결론을 적지 않는다.** 후보에서 막아둔 결론 필드를 여기서
허용하면 우회로가 된다. 대신 `settled`(합의된 사실, 출처 필수), `split`(갈린
지점), `open`(못 푼 것, 이유 필수) 셋으로 나눈다.

`split`이나 `open`이 비어 있으면 검사기가 잡는다. **깨끗하게 합의된 회차는
잘된 게 아니라 의심스러운 것**이고, 다 풀렸다는 것은 대개 한 단계 덜 판
것이며 실무적으로는 다음 후보가 나오지 않는다는 뜻이다.

`sessions balance`가 배치 규칙을 실제 이력에 대고 검사해서 다음 회차의
권장 축과 금지 축을 낸다. `sessions similar`는 이미 다룬 주제인지 보여주되
자동으로 거르지 않는다 — 반복인지 후속인지는 사람만 안다.

`topics validate`가 검사하는 것은 코드 품질이 아니라 모임의 형식이다.

- 후보에 결론 필드(`conclusion`, `결론`, `전망` …)가 있으면 거부한다.
  후보 단계에서 답을 적으면 세션은 토론이 아니라 발표가 된다
- `splits`가 2개 미만이면 거부한다. 찬반이 안 갈리면 토론할 게 없다
- `why_now`의 모든 항목에 출처를 요구한다. 없으면 그냥 인상이다
- `data.ready: partial`이면 무엇이 부족한지 적게 한다
- 개인 축(4·5·6)이라도 운영자 준비물을 적게 한다. "각자 알아서"로 두면
  빈손으로 나가게 된다
- 6축 중 후보가 없는 축을 보고한다
- 포착 단계에서 `verify`가 비어 있으면 잡는다. 반증 불가능한 주장은 가장
  싼 시점에 걸러야 한다

실제로 이 검사기가 초안에서 두 건을 잡았다. 질문 뒤에 서술문이 붙은 후보와,
부족한 데이터를 안 적은 후보였다.

**뉴스 수집은 자동화하지 않는다.** 자동으로 모으면 많이 나온 것이 올라오는데
그게 정확히 피하려던 것이다. 포착은 사람이 30초 쓰는 편이 낫고, 같은 통로를
참가자에게 열어두는 것(`--origin "참가자 제안" --by`)이 수요자 주도의 실제
구현이다.

## 데이터 소스

| 소스 | 데이터 | 필요한 키 |
|---|---|---|
| DART | 공시, 주요계정, 배당 사항 | `DART_API_KEY` |
| KRX | 아래 15종 | `KRX_OPENAPI_KEY` |
| ECOS | 한국 거시 전반 — 통계표 834종 | `ECOS_API_KEY` |

### ECOS: 통계코드를 코드에 박지 않는다

KRX·DART에는 기준금리, 물가, 통화량, 환율이 하나도 없다. 시장 국면을 다루는
회차는 ECOS 없이 성립하지 않는다.

ECOS는 지표가 바뀌어도 **통계코드만 바뀌는** 구조라 이 레이어와 잘 맞는다.
어댑터는 "무엇이 중요한지" 대신 "어떻게 찾는지"만 안다. 그래서 탐색이
어댑터의 기능으로 들어가 있다.

```bash
# 1. 통계코드를 찾는다
python3 cli.py ecos tables --search 소비자물가지수     # → 901Y009
python3 cli.py ecos keystat --search 금리              # 100대 지표 최신값

# 2. 항목코드를 찾는다 (대부분 통계코드만으로는 부족하다)
python3 cli.py ecos items 901Y009 --search 총지수      # → 0

# 3. 시계열을 받는다
python3 cli.py ecos series 901Y009 --items 0 --cycle M --start 202301 --end 202608 --save
python3 cli.py ecos series 722Y001 --items 0101000 --cycle M --start 202401 --end 202608
```

주기는 `A S Q M SM D`, 기간 형식은 주기를 따른다(`2025` / `2025Q1` / `202501` /
`20250131`). 날짜는 **기간 말일**로 정규화한다. 월초로 두면 `yoy`가 달력 연산으로
떨어져 2월과 윤년에서 조용히 틀린다.

`items()`가 알려주는 `START_TIME`~`END_TIME`을 먼저 보는 편이 좋다. 같은 항목이
주기별로 제공 기간이 다르다.

**인증키가 URL 경로에 들어간다.** 쿼리스트링만 지우는 것으로는 부족해서,
환경변수에 있는 자격증명을 기록 직전에 전부 치환한다(`config.scrub`).
캐시 241개 파일 전수 검사로 확인했고 회귀 테스트로 고정해뒀다.

### KRX는 두 개가 있다

이름이 겹쳐서 반드시 구분해야 한다.

| | KRX 정보데이터시스템 | 공공데이터포털 |
|---|---|---|
| 호스트 | `data-dbg.krx.co.kr` | `apis.data.go.kr` |
| 인증 | `AUTH_KEY` **헤더** | `serviceKey` 쿼리 파라미터 |
| 키 형식 | 40자 16진수 | 길고 특수문자 포함 |
| 조회 단위 | `basDd` 하루치 전체 | `beginBasDt`~`endBasDt` 기간 |

이 저장소는 **앞쪽**을 쓴다. 뒤쪽 키를 넣으면 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`가
아니라 그냥 401이 난다. 서비스 신청은 data.krx.co.kr > 오픈API > 이용현황에서 한다.
승인되지 않은 데이터셋은 401을 반환하고, 어떤 서비스를 신청해야 하는지 안내한다.

| dataset | 내용 |
|---|---|
| `stock_kospi` `stock_kosdaq` | 전종목 OHLCV, 시총, 상장주식수 |
| `info_kospi` `info_kosdaq` | 종목기본정보 — 업종, 상장일, 액면가 |
| `index_kospi` `index_kosdaq` `index_krx` | 지수 시리즈 시세 |
| `index_bond` | 채권지수, 듀레이션, 평균 수익률 |
| `etf` | ETF 시세, NAV, 추적지수 괴리 |
| `bond_kts` | 국고채 — 만기별 지표물 수익률(`CLSPRC_YD`) |
| `bond_general` `bond_small` | 일반·소액채권시장 |
| `gold` `oil` | 금·석유시장 |
| `option_equity` | 주식옵션 — 내재변동성(`IMP_VOLT`) |

### 하루치 전체만 준다는 점

기간 조회가 없고 `isuCd` 필터도 무시된다. 시계열은 거래일마다 한 번씩
호출해서 조립한다. 느려 보이지만 **캐시 단위가 하루치 전종목**이라 같은 기간을
다시 볼 때는 종목이 몇 개든 공짜다. 실측으로 2개월 구간 첫 종목 75초,
두 번째 종목 4초, 세 번째 2초였다.

휴장일은 오류가 아니라 0행으로 온다. 그래서 **공휴일 달력을 코드에 넣지 않는다.**
데이터 부재가 곧 답이다.

### 아직 없는 것

- **ETF 구성종목(PDF)** — MDC 회원 로그인(`KRX_ID`/`KRX_PW`) 뒤에만 있고 쿠키
  세션이 필요하다. 인증 방식을 하나로 유지하려고 뺐다. `etf` 데이터셋의
  시세·NAV는 키 없이 쓸 수 있다
- FRED, CFTC, 일본 재무성 — 어댑터만 추가하면 된다. `docs/LAYERS.md` 참고

## 테스트

```bash
python3 -m unittest discover -s tests -t .
```

80개. 네트워크가 필요 없다. 대부분은 실제로 한 번 물렸던 곳을 고정한 것이다 —
월말 인덱스 시프트, 중복 날짜, 자격증명 유출, 후보 풀의 결론 유입.

이 테스트들은 장식이 아니라 실제로 두 개의 조용한 오류를 잡았다.

**월말 인덱스.** `2024-04-30`에서 한 달을 빼면 `2024-03-30`이 되어 전월말인
`03-31`을 찾지 못한다. 윤년 2월은 더 나쁘다. 월간 매크로 시계열은 거의 다
월말 기준이므로 `transform._shifted()`는 월 단위 비교를 기간(period) 공간에서 한다.

**중복 날짜.** 10년 국고채 지표물 필터에 명목채와 물가연동채가 함께 걸려
하루에 두 값이 들어갔다. 3년물과의 스프레드가 그럴듯한 숫자로 나왔지만
실질금리와 명목금리를 섞은 값이었다. `Series`는 이제 같은 날짜에 값이 둘
이상이면 충돌하는 값을 함께 보여주며 거부한다.

## 구조

```
core/          Layer 1 — 결정론적. 국면과 무관한 것만
  sources/     소스별 fetch 어댑터
  http.py      재시도 · 백오프 · rate limit
  cache.py     원본 응답 보관 (감사 기록)
  series.py    출처가 붙어 다니는 데이터 캐리어
  transform.py 산술 primitive

sessions/      Layer 2 — 회차별 명세 (Step 2)
agents/        Layer 3 — AI 역할별 프롬프트 (Step 4)
study/         운영 축적: 국면 가설, 주제 좌표, 참여 지표 (Step 5~6)

cache/         gitignore. 재현 가능하므로 커밋하지 않는다
series/        CSV + 출처 사이드카. 회차 감사를 위해 커밋한다
```
