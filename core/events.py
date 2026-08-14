"""주장 → 사건 묶기 (V0).

## 앞선 판본이 틀렸던 세 가지

첫 판본은 "같은 날 + 2개 이상 매체 + 토큰 겹침"을 사건의 정의로 썼다.
돌려보니 이름표가 `지난달`, `기록했다` 로 나왔다. 불용어를 더 넣는 것으로
고칠 문제가 아니었다. 세 가지가 같이 틀려 있었다.

**매체 수를 관문으로 썼다.** 한국은행 발표 하나, 좋은 리서치 하나만으로도
스터디는 열린다. 매체가 몇 곳이냐는 사건이 성립하느냐가 아니라 **얼마나
믿을 만하냐**의 문제다. 그래서 지금은 관문이 아니라 confidence 의 재료다.

**하루로 잘랐다.** 월요일 총재 발언, 화요일 엔화 급등, 수요일 포지션 청산은
한 줄기 이야기다. 하루로 자르면 세 조각이 된다. 지금은 WINDOW 일 안을 본다.

**흔한 토큰으로 묶었다.** `기록했다` 는 거의 모든 주장에 나오므로 무엇이든
무엇과든 묶어 버린다. 지금은 자주 나오는 토큰일수록 가중치를 낮춰
(idf) 드문 토큰이 묶음과 이름표를 정하게 한다.

## 사건과 주제는 다르다

여기서 나온 Event 를 스터디 주제와 같은 것으로 취급하지 않는다.

    주장 → 사건 → 줄기 → 스터디 주제

알파벳의 AI 회사채 발행과 메타의 AI 설비투자 확대는 **같은 사건이 아니다.**
그러나 "하이퍼스케일러가 외부 자금으로 AI 투자를 늘린다"는 **같은 줄기**다.
같은 사건으로 묶으면 사실이 왜곡되고, 안 묶으면 좋은 주제를 놓친다.
줄기 계층은 아직 만들지 않았지만, 그 자리를 미리 비워 둔다.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from . import newsletters as nl
from .feeds import STOPWORDS
from .sessions import _stem

MIN_TOKEN = 3
MIN_CLAIMS = 2
EVENT_CAP = 12

# 며칠 안을 한 묶음 후보로 볼 것인가. **사건의 정의가 아니라 검색 범위다.**
# 어떤 이야기는 발표 다음 날 끝나고 어떤 이야기는 2주에 걸쳐 쌓인다. 3일에
# 무슨 근거가 있는 게 아니라, 지금 후보 수를 감당할 만큼 줄이는 값일 뿐이다.
# 나중에 줄기(theme) 계층을 만들면 거기는 훨씬 긴 창을 쓰게 된다.
WINDOW = 3

# 너무 흔한 토큰은 무엇이든 묶어 버리므로 씨앗에서 뺀다.
MAX_DF_RATIO = 0.20

# 다만 빈도만으로는 안 걸러진다. 실측하면 `상승했다` 4.0%, `반도체` 3.5%,
# `코스닥` 7.0% 다. 문턱을 낮추면 가장 쓸모 있는 `코스닥` 이 먼저 죽는다.
# 갈라야 할 것은 빈도가 아니라 품사다. `기록했다` 는 무엇이 일어났는지
# 아무것도 말해 주지 않는다.
#
# 처음에는 `다` 로 끝나면 서술어로 봤다. 그건 위험하다. 혼다, 마쓰다, 소다는
# 개체명이면서 `다` 로 끝난다. 그래서 끝에서 두 번째 음절이 활용 어미인지를
# 본다. `했` `됐` `진` 같은 것들이다. 혼다의 `혼`, 마쓰다의 `쓰` 는 여기
# 없으므로 살아남는다.
#
# 이 규칙은 **검색과 표시에만 쓴다.** 두 사건이 같은 사건이냐는 판단에
# 형태 규칙을 진리로 쓰지 않는다. 그건 의미로 봐야 하고 다음 단계 몫이다.
CONJUGATION = set(
    "했 됐 았 었 였 렸 났 쳤 왔 혔 웠 랐 겼 켰 섰 냈 췄 줬 뒀 봤 꿨 뤘 셨 "
    "한 된 진 인 는 난 긴 른 린 온 든 준 낸 "
    "이 하 보 겠 시".split())


def is_predicate(token: str) -> bool:
    return len(token) >= 2 and token.endswith("다") and token[-2] in CONJUGATION

# 검증 경로별 증거 강도. 사건 순위를 매길 때 쓴다.
MODE_WEIGHT = {
    "direct_data": 1.0,
    "primary_source": 0.8,
    "multi_source_research": 0.6,
    "future_tracking": 0.3,
    "interpretive": 0.2,
    "not_researchable": 0.0,
}


@dataclass
class Event:
    event_id: str
    label: str
    day: str                       # 가장 이른 날
    last_day: str = ""             # 가장 늦은 날. 다르면 여러 날 걸친 사건
    tokens: list[str] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    adapter_verifiable: int = 0
    evidence: float = 0.0          # 검증 경로 가중 합

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def spans_days(self) -> bool:
        return bool(self.last_day) and self.last_day != self.day

    @property
    def confidence(self) -> float:
        """이 묶음을 하나의 사건으로 믿을 만한가. 0~1.

        매체 수는 여기 들어오지 뿐 생성 관문이 아니다. 단독 보도도 사건이
        되되 confidence 가 낮게 나와 아래로 밀린다.
        """
        breadth = min(self.source_count / 3, 1.0)
        mass = min(len(self.claims) / 4, 1.0)
        ground = min(self.evidence / max(len(self.claims), 1), 1.0)
        return round(0.4 * breadth + 0.3 * mass + 0.3 * ground, 3)


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text)
    out = set()
    for w in raw:
        s = _stem(w.lower()) if re.match(r"[가-힣]", w) else w.lower()
        if len(s) >= MIN_TOKEN and s not in STOPWORDS:
            out.add(s)
    return out


def _parse_day(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _near(a: str, b: str) -> bool:
    """두 날짜가 한 사건으로 볼 만큼 가까운가."""
    da, db = _parse_day(a), _parse_day(b)
    if not da or not db:
        return a == b
    return abs((da - db).days) <= WINDOW


def cluster(letters: list[nl.Newsletter], *, day: str | None = None) -> list[Event]:
    rows: list[tuple[str, nl.Newsletter, nl.Item]] = []
    for L in letters:
        d = nl._day(L)
        if day and not _near(d, day):
            continue
        for item in L.claims:
            if item.verification_mode == "not_researchable":
                continue
            rows.append((d, L, item))
    if not rows:
        return []

    # 토큰별 등장 주장. 흔한 토큰은 여기서 걸러낸다.
    index: dict[str, list[int]] = defaultdict(list)
    tokens_of: list[set[str]] = []
    for n, (_, _, item) in enumerate(rows):
        toks = _tokens(f"{item.headline} {item.text}")
        tokens_of.append(toks)
        for t in toks:
            index[t].append(n)

    total = len(rows)
    cap = max(2, int(total * MAX_DF_RATIO))
    idf = {t: math.log(total / len(hits)) for t, hits in index.items()
           if len(hits) <= cap and not is_predicate(t)}

    scored = []
    for tok, weight in idf.items():
        hits = index[tok]
        if len(hits) < MIN_CLAIMS:
            continue
        days = {rows[n][0] for n in hits}
        if not all(_near(x, y) for x in days for y in days):
            continue
        sources = {rows[n][1].name for n in hits}
        # 드문 토큰 우선. 매체 수는 순위에만 쓰고 통과 조건이 아니다.
        scored.append((weight * len(hits), tok, hits, sources))
    scored.sort(reverse=True, key=lambda x: (x[0], len(x[3])))

    events: list[Event] = []
    used: set[int] = set()
    for _, tok, hits, sources in scored:
        fresh = [n for n in hits if n not in used]
        if len(fresh) < MIN_CLAIMS:
            continue
        used.update(fresh)

        payload, av, evidence = [], 0, 0.0
        for n in fresh:
            _, L, item = rows[n]
            payload.append({
                "source": L.name,
                "headline": item.headline or item.text[:60],
                "claim": item.text,
                "claim_type": item.claim_type,
                "verification_mode": item.verification_mode,
                "provenance": item.provenance,
                "block": item.block,
                "source_span": item.source_span,
                "adapter_verifiable": item.adapter_verifiable,
            })
            av += bool(item.adapter_verifiable)
            evidence += MODE_WEIGHT.get(item.verification_mode, 0.4)

        days = sorted(rows[n][0] for n in fresh)
        distinctive = _distinctive(fresh, tokens_of, idf) or [tok]
        events.append(Event(
            event_id=f"E-{len(events) + 1:03d}",
            label=" · ".join(distinctive[:3]),
            day=days[0],
            last_day=days[-1],
            tokens=distinctive,
            claims=payload,
            sources=sorted({rows[n][1].name for n in fresh}),
            adapter_verifiable=av,
            evidence=round(evidence, 2),
        ))
        if len(events) >= EVENT_CAP:
            break

    events.sort(key=lambda e: (-e.confidence, e.day))
    for n, e in enumerate(events, 1):
        e.event_id = f"E-{n:03d}"
    return events


SIGNATURE = 8


def _distinctive(members: list[int], tokens_of: list[set[str]],
                 idf: dict[str, float]) -> list[str]:
    """이 묶음을 다른 묶음과 구별해 주는 토큰들.

    앞의 셋이 이름표가 되고, 전체가 사건끼리 비교할 때의 지문이 된다.
    씨앗 토큰 하나만 쓰면 `기록했다` 같은 것이 이름이 된다. 묶음에 속한
    주장들이 **공통으로** 가진 토큰 중 드문 것을 골라야 사건이 드러난다.
    """
    shared: dict[str, int] = defaultdict(int)
    for n in members:
        for t in tokens_of[n]:
            if t in idf:
                shared[t] += 1
    ranked = sorted(shared.items(),
                    key=lambda kv: (-kv[1] * idf.get(kv[0], 0), kv[0]))
    return [t for t, _ in ranked[:SIGNATURE]]


# --- 사건 쌍: 찾기와 판정은 다른 일이다 -----------------------------------
#
# 사건 관계를 맞히는 일은 두 단계다.
#
#     찾기(retrieval)   비교해 볼 만한 쌍을 골라 올린다
#     판정(adjudication) 올라온 쌍이 같은 사건인지 정한다
#
# 이 둘을 한 숫자로 보면 안 된다. 찾기가 진짜 같은 사건인 쌍을 아예
# 안 올리면 판정이 아무리 똑똑해도 소용없다. 그 쌍은 판정자에게
# 도착조차 하지 않는다. 나중에 "Luna 가 왜 이걸 놓쳤지" 하고 볼 때
# 사실은 앞 단계 문제인 경우를 가려내려면 따로 재야 한다.
#
#     찾기   재현율 — 실제 양성 중 몇 개나 올렸나
#     판정   분류별 정밀도·재현율
#
# 아래 문턱과 창은 전부 **찾기 매개변수**다. 사건이 무엇이냐는 정의가
# 아니다. 3일이라는 창도 마찬가지다. 어떤 이야기는 2주에 걸쳐 쌓인다.

SAME = 0.35
RELATED = 0.10

# 각 겹침 구간에서 몇 쌍씩 가져올까. 한쪽으로 쏠린 표본을 만들지 않으려는
# 장치다. 다만 이걸로 정답 분포를 맞출 수는 없다 — 정답을 아직 모르니까.
BANDS = ((0.0, 0.01), (0.01, 0.05), (0.05, 0.10),
         (0.10, 0.20), (0.20, 0.35), (0.35, 1.01))

# 이만큼도 안 남는 주장끼리는 겹침을 재지 않는다. 토큰이 하나뿐이면
# 그 하나가 겹치는 순간 자카드가 1.0 이 되어 버린다.
MIN_SIGNAL = 3


def all_pairs(events: list[Event]) -> list[dict]:
    """가능한 모든 사건 쌍. 겹침을 재서 붙여 둔다.

    비교는 사건을 정의한 지문끼리 한다. 두 사건의 모든 주장을 통째로 섞어
    비교하면 주변 단어에 묻혀 무엇이든 남남으로 나온다. 실제로 그렇게 했을
    때 66쌍 중 65쌍이 unrelated 였고, 거기에 `반도체·메모리·cxmt` 와
    `메모리·cxmt` 처럼 누가 봐도 같은 사건인 쌍이 섞여 있었다.
    """
    out = []
    for i in range(len(events)):
        for j in range(i + 1, len(events)):
            a, b = events[i], events[j]
            ta, tb = set(a.tokens), set(b.tokens)
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / len(ta | tb)
            out.append({
                "a": a.event_id, "b": b.event_id,
                "a_label": a.label, "b_label": b.label,
                "a_claim": a.claims[0]["claim"] if a.claims else "",
                "b_claim": b.claims[0]["claim"] if b.claims else "",
                "token_overlap": round(overlap, 3),
                "same_day": a.day == b.day,
            })
    return out


def retrieved(pair: dict) -> bool:
    """찾기 단계가 이 쌍을 판정자에게 올리는가."""
    return pair["token_overlap"] >= RELATED


def guess(pair: dict) -> str:
    """겹침만 보고 찍는다. 판정자 자리를 채워 둔 임시방편이다.

    이 값을 정답 옆에 나란히 두는 이유는 하나다. 나중에 Luna 를 붙였을 때
    "그냥 겹침으로 찍는 것보다 나은가"를 물어야 하기 때문이다. 기준선이
    없으면 Luna 가 잘하는지 알 수 없다.
    """
    overlap = pair["token_overlap"]
    if overlap >= SAME:
        return "same_event"
    if overlap >= RELATED:
        return "related_theme"
    return "unrelated"


def claim_pairs(letters, *, limit: int = 80, seed: int = 7) -> list[dict]:
    """사람이 판정할 **주장 쌍**을 뽑는다.

    처음에는 사건끼리 짝지었다. 그건 단위가 틀렸다. 묶는 일을 이미 끝낸
    결과물끼리 비교하는 것이라, 같은 사건인 쌍이 나올 수가 없다. 실제로
    12개 사건에서 66쌍을 만들었더니 56쌍이 아예 겹치는 단어가 없었다.

    판정해야 할 것은 **두 주장이 한 사건인가**다. 묶는 기계가 답하는 질문이
    바로 그것이기 때문이다. 그래야 이렇게 잴 수 있다.

        찾기   기계가 이 둘을 같은 사건에 넣었나 (retrieved)
        정답   사람이 보기에 같은 사건인가 (label)

    둘이 어긋나는 지점이 곧 고칠 곳이다.

    880개 주장의 모든 쌍은 38만 쌍이라 다 볼 수 없다. 단어를 하나라도
    공유하는 쌍은 역색인으로 추려내고, 아무것도 안 겹치는 쌍은 무작위로
    섞는다. 겹치는 쌍만 모으면 쉬운 문제만 남는다.
    """
    import random

    rows: list[tuple[str, str, str]] = []      # (day, source, text)
    index: dict[str, list[int]] = defaultdict(list)
    tokens_of: list[set[str]] = []
    for L in letters:
        day = nl._day(L)
        for item in L.claims:
            if item.verification_mode == "not_researchable":
                continue
            n = len(rows)
            rows.append((day, L.name, item.text))
            toks = _tokens(f"{item.headline} {item.text}")
            tokens_of.append(toks)
            for t in toks:
                index[t].append(n)

    together = _co_located(letters, rows)

    seen: set[tuple[int, int]] = set()
    overlapping: list[tuple[int, int, float]] = []
    for hits in index.values():
        if len(hits) > 60:                     # 흔한 단어는 아무나 이어 준다
            continue
        for x in range(len(hits)):
            for y in range(x + 1, len(hits)):
                key = (hits[x], hits[y])
                if key in seen or not _near(rows[key[0]][0], rows[key[1]][0]):
                    continue
                seen.add(key)
                ta, tb = tokens_of[key[0]], tokens_of[key[1]]
                # 짧은 주장은 남는 토큰이 한둘뿐이라 그 하나가 겹치면
                # 자카드가 1.0 이 된다. "퇴출 사례가 나올 가능성"과
                # "피해 규모가 커질 가능성"이 만점으로 붙어 버린다.
                if min(len(ta), len(tb)) < MIN_SIGNAL:
                    continue
                overlapping.append((*key, len(ta & tb) / len(ta | tb)))

    rng = random.Random(seed)
    far = []
    for _ in range(limit * 6):
        i, j = rng.randrange(len(rows)), rng.randrange(len(rows))
        if i == j or (min(i, j), max(i, j)) in seen:
            continue
        far.append((min(i, j), max(i, j), 0.0))

    buckets: dict[tuple, list] = {b: [] for b in BANDS}
    for i, j, ov in overlapping + far:
        for lo, hi in BANDS:
            if lo <= ov < hi:
                buckets[(lo, hi)].append((i, j, ov))
                break
    for rowset in buckets.values():
        rowset.sort(key=lambda t: -t[2])

    picked, n = [], 0
    deepest = max((len(r) for r in buckets.values()), default=0)
    while len(picked) < limit and n < deepest:
        for band in BANDS:
            if n < len(buckets[band]) and len(picked) < limit:
                picked.append(buckets[band][n])
        n += 1

    out = []
    for i, j, ov in picked:
        pair = tuple(sorted((i, j)))
        out.append({
            "pair_id": f"C-{len(out) + 1:03d}",
            "a_source": rows[i][1], "a_day": rows[i][0], "a_claim": rows[i][2],
            "b_source": rows[j][1], "b_day": rows[j][0], "b_claim": rows[j][2],
            "token_overlap": round(ov, 3),
            "same_day": rows[i][0] == rows[j][0],
            # 기계가 실제로 이 둘을 한 사건에 넣었는가
            "retrieved": pair in together,
            "heuristic": "same_event" if pair in together else "unrelated",
            "label": None,      # same_event | related_theme | unrelated
            "note": "",
        })
    return out


def _co_located(letters, rows) -> set[tuple[int, int]]:
    """묶는 기계가 실제로 같은 사건에 넣은 주장 쌍."""
    where = {(r[1], r[2]): n for n, r in enumerate(rows)}
    out = set()
    for event in cluster(letters):
        members = [where.get((c["source"], c["claim"])) for c in event.claims]
        members = [m for m in members if m is not None]
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                out.add(tuple(sorted((members[x], members[y]))))
    return out


def pair_sample(events: list[Event], *, limit: int = 80) -> list[dict]:
    """사람이 판정할 쌍을 겹침 구간마다 고르게 뽑는다.

    한쪽으로 쏠린 표본으로는 아무것도 못 잰다. 문제 66개 중 65개의 답이
    같으면 전부 그 답으로 찍어도 98점이 나온다.

    다만 **정답 분포를 맞출 수는 없다.** 정답을 아직 모르기 때문이다.
    할 수 있는 것은 겹침이라는 연속값을 구간으로 나눠 골고루 담는 것뿐이다.
    한 바퀴 라벨을 붙이고 나면 그때 진짜 분포를 보고 다시 뽑을 수 있다.

    찾기가 안 올린 쌍(겹침이 문턱 아래)도 일부러 섞는다. 그 안에 진짜
    같은 사건이 들어 있으면 그게 곧 찾기 단계가 놓친 것이고, 그걸 세야
    찾기 재현율이 나온다.
    """
    pool = all_pairs(events)
    buckets: dict[tuple, list[dict]] = {b: [] for b in BANDS}
    for p in pool:
        for lo, hi in BANDS:
            if lo <= p["token_overlap"] < hi:
                buckets[(lo, hi)].append(p)
                break
    for rows in buckets.values():
        rows.sort(key=lambda p: -p["token_overlap"])

    picked, n = [], 0
    deepest = max((len(r) for r in buckets.values()), default=0)
    while len(picked) < limit and n < deepest:
        for band in BANDS:
            rows = buckets[band]
            if n < len(rows) and len(picked) < limit:
                picked.append(rows[n])
        n += 1

    out = []
    for p in picked:
        out.append({
            "pair_id": f"P-{len(out) + 1:03d}",
            **p,
            "retrieved": retrieved(p),
            "heuristic": guess(p),
            "label": None,          # same_event | related_theme | unrelated
            "note": "",
        })
    return out


def study_candidates(events: list[Event], *, limit: int = 4) -> list[dict]:
    """사건 중 스터디로 열 만한 것 (V0 휴리스틱).

    사건과 주제를 같은 것으로 보지 않으므로 이건 어디까지나 후보다.
    실제 주제는 여러 사건을 하나의 질문으로 묶는 경우가 많다.
    """
    ranked = sorted(
        events,
        key=lambda e: (e.confidence, e.evidence, e.adapter_verifiable),
        reverse=True,
    )
    out = []
    for e in ranked[:limit]:
        modes = {c.get("verification_mode") for c in e.claims}
        out.append({
            "event_id": e.event_id,
            "day": e.day if not e.spans_days else f"{e.day}~{e.last_day}",
            "label": e.label,
            "sources": e.sources,
            "confidence": e.confidence,
            "sample_claim": e.claims[0].get("claim", "") if e.claims else "",
            "claim_types": sorted({c.get("claim_type") for c in e.claims}),
            "verification_modes": sorted(m for m in modes if m),
            "core_question": _question(e),
            "adapter_verifiable": e.adapter_verifiable > 0,
            "single_source": e.source_count == 1,
        })
    return out


def _question(event: Event) -> str:
    types = {c.get("claim_type") for c in event.claims}
    if "causal_claim" in types:
        return f"'{event.label}' 의 인과 설명 중 무엇이 데이터로 확인되고 무엇이 통념인가?"
    if "forecast" in types:
        return f"'{event.label}' 전망의 전제 중 이미 깨진 것은 무엇인가?"
    if "market_observation" in types or "numeric_fact" in types:
        return f"'{event.label}' 은 수급·지표의 변화인가, 이야기의 변화인가?"
    return f"'{event.label}' 이 지금 다뤄지는 이유는 무엇인가?"
