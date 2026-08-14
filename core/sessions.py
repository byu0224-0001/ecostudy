"""회차 이력.

후보 풀이 "무엇을 할까"를 담는다면 여기는 "무엇을 했나"를 담는다. 이게
없으면 두 가지가 무너진다. 반년 뒤 "작년에 하지 않았나"를 기억에 의존하게
되고, 6축 배분 규칙이 문서에만 남는다. 26회를 돌리면 반드시 걸린다.

## 결론을 적지 않는 이유

가장 중요한 설계 결정이다. 회차 기록에 `conclusion` 필드를 두지 않는다.

이 모임의 목적은 답을 내는 것이 아니라 각자 자기 기준을 세우는 것이다.
"결론: 엔캐리 청산 위험은 제한적" 이라고 적는 순간 그 회차는 발표가 되고,
다음에 그 주제를 다시 볼 때 이미 답이 있으니 볼 필요가 없어진다.

대신 셋으로 나눠 적는다.

    settled  그 자리에서 사실로 합의된 것 — 출처가 있어야 한다
    split    갈린 지점과 각 입장 — 이게 그 회차의 알맹이다
    open     못 푼 것 — 이게 다음 회차 후보가 된다

**깨끗하게 합의된 회차는 잘된 게 아니라 의심스러운 것이다.** 아무도 갈리지
않았다면 주제 선정이 틀렸거나 아무도 반대하지 않은 것이다. 그래서 `split`
이 비어 있으면 검사기가 잡는다.

`open` 이 비어 있어도 잡는다. 다 풀렸다는 것은 대개 한 단계 덜 판 것이고,
실무적으로는 후보 유입이 끊긴다는 뜻이다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config
from .topics import AXES, BANNED_FIELDS, JUDGMENTS

DIR = config.ROOT / "sessions"

REQUIRED = ("id", "date", "axis", "title", "question")

# 배치 규칙(docs/TOPICS.md)을 주 단위로 옮긴 것. 분기는 13주로 본다.
QUARTER_WEEKS = 13
BOOK_WEEKS = 8
CADENCE_WEEKS = 2

PERSONAL_AXES = (4, 5, 6)
ANCHOR_AXIS = 1

TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
# 겹침을 재는 데 방해가 되는 말. 주제어가 아니라 문장 골격이다.
SKIP = {
    "무엇", "어떻게", "어디", "언제", "그것", "이것", "우리", "지금", "정말",
    "있는", "없는", "하는", "되는", "같은", "위해", "대해", "관련", "경우",
    "이번", "다시", "가장", "매우", "모든", "각자", "얼마나", "어느",
}


@dataclass
class Issue:
    session: str
    message: str


@dataclass
class Session:
    raw: dict
    path: Path | None = None

    @property
    def id(self) -> str:
        return str(self.raw.get("id", "?"))

    @property
    def date(self) -> dt.date | None:
        value = self.raw.get("date")
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    @property
    def axis(self) -> int | None:
        value = self.raw.get("axis")
        return value if isinstance(value, int) else None

    @property
    def is_book(self) -> bool:
        return bool(self.raw.get("book"))

    @property
    def open_questions(self) -> list[dict]:
        return self.raw.get("open") or []

    def text(self) -> str:
        """중복 검사에 쓰는 본문. 제목·질문·검증한 주장·갈린 지점."""
        parts = [str(self.raw.get("title", "")), str(self.raw.get("question", ""))]
        for c in self.raw.get("claims_checked") or []:
            parts.append(str(c.get("claim", "")))
        for s in self.raw.get("split") or []:
            parts.append(str(s.get("point", "")))
            parts.extend(str(p) for p in (s.get("positions") or []))
        for o in self.open_questions:
            parts.append(str(o.get("question", "")))
        return " ".join(parts)


@dataclass
class History:
    sessions: list[Session] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.sessions)

    @property
    def ordered(self) -> list[Session]:
        """오래된 것부터. 날짜가 없는 것은 뒤로 밀되 버리지는 않는다."""
        return sorted(self.sessions, key=lambda s: (s.date is None, s.date or dt.date.min))

    def get(self, sid: str) -> Session | None:
        wanted = sid.upper()
        return next((s for s in self.sessions if s.id.upper() == wanted), None)

    def since(self, weeks: int, *, today: dt.date | None = None) -> list[Session]:
        cutoff = (today or dt.date.today()) - dt.timedelta(weeks=weeks)
        return [s for s in self.ordered if s.date and s.date >= cutoff]

    def next_id(self) -> str:
        used = [int(s.id[2:]) for s in self.sessions
                if s.id.upper().startswith("S-") and s.id[2:].isdigit()]
        return f"S-{max(used, default=0) + 1:03d}"


def load(directory: Path | None = None) -> History:
    src = directory or DIR
    if not src.exists():
        return History()
    out = []
    for path in sorted(src.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if raw:
            out.append(Session(raw=raw, path=path))
    return History(sessions=out)


def save(session: Session, directory: Path | None = None) -> Path:
    dst = directory or DIR
    dst.mkdir(parents=True, exist_ok=True)
    path = session.path or dst / f"{session.id}.yaml"
    path.write_text(
        yaml.safe_dump(session.raw, allow_unicode=True, sort_keys=False, width=88),
        encoding="utf-8",
    )
    return path


# --- 검사 -------------------------------------------------------------------

def _check(s: Session) -> list[Issue]:
    out: list[Issue] = []

    for f in REQUIRED:
        if not s.raw.get(f):
            out.append(Issue(s.id, f"필수 필드 없음: {f}"))

    for f in s.raw:
        if f.lower() in BANNED_FIELDS:
            out.append(Issue(s.id, f"회차 기록에도 결론 필드는 두지 않는다: {f}"))

    if s.axis not in AXES:
        out.append(Issue(s.id, f"axis 는 1~6 이어야 한다 (현재 {s.raw.get('axis')!r})"))
    if (j := s.raw.get("judgment")) and j not in JUDGMENTS:
        out.append(Issue(s.id, f"judgment 는 {sorted(JUDGMENTS)} 중 하나 (현재 {j!r})"))
    if s.raw.get("date") and s.date is None:
        out.append(Issue(s.id, f"date 를 읽을 수 없다: {s.raw['date']!r}"))

    # 합의된 사실은 출처가 있어야 한다. 없으면 그 자리의 분위기였을 뿐이다.
    for i, item in enumerate(s.raw.get("settled") or []):
        if isinstance(item, str) or not item.get("source"):
            out.append(Issue(s.id, f"settled[{i}] 에 source 가 없다"))

    # 아무도 갈리지 않은 회차는 잘된 게 아니라 의심스러운 것이다.
    if not s.raw.get("split") and not s.is_book:
        out.append(Issue(s.id, "split 이 비어 있다 — 아무도 갈리지 않았다면 "
                               "주제가 틀렸거나 토론이 안 된 것이다"))

    # open 이 마르면 후보 유입이 끊긴다.
    if not s.open_questions:
        out.append(Issue(s.id, "open 이 비어 있다 — 다 풀렸다는 것은 대개 "
                               "한 단계 덜 판 것이고, 다음 후보가 나오지 않는다"))

    for i, o in enumerate(s.open_questions):
        if isinstance(o, str) or not o.get("question"):
            out.append(Issue(s.id, f"open[{i}] 에 question 이 없다"))
        elif not o.get("why"):
            out.append(Issue(s.id, f"open[{i}] 에 why 가 없다 — 왜 못 풀었는지"
                                   " 적어야 다음에 준비할 것이 정해진다"))

    return out


def validate(history: History) -> list[Issue]:
    issues: list[Issue] = []

    seen: dict[str, int] = {}
    for s in history.sessions:
        seen[s.id] = seen.get(s.id, 0) + 1
    for sid, n in seen.items():
        if n > 1:
            issues.append(Issue(sid, f"id 가 {n}번 중복된다"))

    for s in history.sessions:
        issues.extend(_check(s))

    # 같은 축 2회 연속 금지
    ordered = [s for s in history.ordered if s.axis and not s.is_book]
    for prev, curr in zip(ordered, ordered[1:]):
        if prev.axis == curr.axis:
            issues.append(Issue(curr.id, f"{prev.id} 과 같은 {curr.axis}축이 연속된다"))

    return issues


# --- 배치 -------------------------------------------------------------------

@dataclass
class Rule:
    name: str
    ok: bool
    detail: str


def placement(history: History, *, today: dt.date | None = None) -> list[Rule]:
    """배치 규칙(docs/TOPICS.md)이 지금 지켜지고 있는지."""
    today = today or dt.date.today()
    recent = history.since(QUARTER_WEEKS, today=today)
    topics = [s for s in recent if not s.is_book]
    last = next((s for s in reversed(history.ordered) if not s.is_book), None)

    rules: list[Rule] = []

    if last and last.axis:
        rules.append(Rule(
            "직전 축 확인", True,
            f"직전은 {last.id} {last.axis}축({AXES[last.axis]}). "
            "뉴스가 같은 축을 가리키면 반복해도 된다",
        ))

    personal = [s for s in topics if s.axis in PERSONAL_AXES]
    rules.append(Rule(
        "4·5·6축 분기 1회 이상", bool(personal),
        f"지난 분기 {len(personal)}회"
        + (f" ({', '.join(s.id for s in personal)})" if personal
           else " — 목적함수에 가장 직접적인 축인데 비어 있다"),
    ))

    anchor = [s for s in topics if s.axis == ANCHOR_AXIS]
    rules.append(Rule(
        "1축 분기 1회 이상", bool(anchor),
        f"지난 분기 {len(anchor)}회"
        + ("" if anchor else " — 공통 좌표가 없으면 나머지 대화가 흩어진다"),
    ))

    books = [s for s in history.ordered if s.is_book and s.date]
    last_book = books[-1] if books else None
    gap = (today - last_book.date).days // 7 if last_book else None
    rules.append(Rule(
        f"독서 {BOOK_WEEKS}주 이내", gap is not None and gap <= BOOK_WEEKS,
        f"마지막 독서 {last_book.id} 이후 {gap}주" if last_book else "독서 회차 없음",
    ))

    return rules


def coverage(history: History, *, weeks: int = QUARTER_WEEKS,
             today: dt.date | None = None) -> dict[int, int]:
    out = {a: 0 for a in AXES}
    for s in history.since(weeks, today=today):
        if s.axis in out:
            out[s.axis] += 1
    return out


def suggest_axes(history: History, *, today: dt.date | None = None
                 ) -> tuple[list[int], list[int]]:
    """다음 회차에 (권장 축, 직전과 같은 축).

    **여기서 나오는 것은 권고이지 제약이 아니다.** 둘째 값을 오래 `blocked`
    라고 불렀는데 그 이름이 틀렸다. 그날 시장의 이슈가 전부 1축인데 균형이
    3축을 가리킨다면, 이기는 쪽은 뉴스다. 균형은 커리큘럼을 짜는 공급자의
    논리이고 우리가 만들려던 것은 수요 주도다.

    그렇다고 균형을 버리지는 않는다. 매 회차 막는 대신 분기마다 `skew()`
    로 돌아본다. 사전에 차단하면 뉴스를 이기고, 사후에 돌아보면 뉴스를
    이기지 않으면서도 편중을 잡을 수 있다.
    """
    today = today or dt.date.today()
    recent = [s for s in history.since(QUARTER_WEEKS, today=today)
              if not s.is_book]
    last = next((s for s in reversed(history.ordered) if not s.is_book), None)

    repeated = [last.axis] if last and last.axis else []

    covered = {s.axis for s in recent}
    urgent = [a for a in PERSONAL_AXES if a not in covered]
    if ANCHOR_AXIS not in covered:
        urgent.append(ANCHOR_AXIS)

    wanted = [a for a in urgent if a not in repeated]
    if not wanted:
        counts = coverage(history, today=today)
        fewest = min(counts[a] for a in AXES if a not in repeated)
        wanted = [a for a in AXES if a not in repeated and counts[a] == fewest]

    return wanted, repeated


# 분기에 이만큼도 안 나온 축은 사실상 안 다룬 것이다.
STARVED = 1


@dataclass
class Skew:
    counts: dict[int, int]
    starved: list[int]
    dominant: int | None
    total: int

    @property
    def lopsided(self) -> bool:
        """한 축이 분기의 절반을 넘게 가져갔나."""
        if not self.total or self.dominant is None:
            return False
        return self.counts[self.dominant] * 2 > self.total


def skew(history: History, *, today: dt.date | None = None) -> Skew:
    """분기 축 편중 회고.

    뉴스를 따라가면 편중은 반드시 생긴다. 시장이 한동안 한 가지 이야기만
    하는 시기가 있기 때문이다. 그걸 매번 막는 대신 분기마다 확인하고,
    쏠렸다면 다음 분기에 굶은 축부터 의도적으로 채운다.

    이 함수는 아무것도 막지 않는다. 무엇이 비었는지 보여줄 뿐이다.
    """
    counts = coverage(history, today=today)
    total = sum(counts.values())
    starved = [axis for axis, n in counts.items() if n < STARVED]
    dominant = max(counts, key=lambda a: counts[a]) if total else None
    return Skew(counts=counts, starved=starved, dominant=dominant, total=total)


# --- 중복 -------------------------------------------------------------------

# 형태소 분석기를 붙이지 않는 대신 조사만 떼어낸다. "엔캐리와"와 "엔캐리"가
# 다른 토큰이면 중복 검사가 통째로 헛돈다. 실제로 첫 구현이 그랬다.
# 어간이 두 글자 미만으로 줄어들면 떼지 않는다 — "물가"에서 '가'를 떼면
# 남는 것이 없다. 완전하지 않지만 대부분의 명사구를 맞춘다.
PARTICLES = (
    "이라는", "으로써", "에서는", "에게는",
    "이라", "라는", "에서", "으로", "부터", "까지", "에게", "처럼", "보다",
    "마다", "조차", "이나", "이란", "께서",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로",
    "만", "나", "야",
)
MIN_STEM = 2


def _stem(word: str) -> str:
    for particle in PARTICLES:
        if word.endswith(particle) and len(word) - len(particle) >= MIN_STEM:
            return word[: -len(particle)]
    return word


def _tokens(text: str) -> set[str]:
    out = set()
    for t in TOKEN.findall(text or ""):
        word = t.lower() if t.isascii() else _stem(t)
        if word not in SKIP and len(word) >= MIN_STEM:
            out.add(word)
    return out


MIN_QUERY_TOKENS = 3


@dataclass
class Match:
    session: Session
    score: float
    shared: list[str]


def similar(history: History, text: str, *, top: int = 3,
            floor: float = 0.20) -> list[Match]:
    """비슷한 과거 회차를 찾는다.

    묻는 것은 "이 주장이 이미 다뤄졌나"이지 "두 글이 얼마나 닮았나"가
    아니다. 그래서 자카드가 아니라 포함률을 쓴다. 회차 기록은 제목·질문·
    갈린 지점·미해결까지 담아 토큰이 수십 개인데 주장은 열 개 남짓이라,
    합집합으로 나누면 여섯 개가 겹쳐도 점수가 바닥에 깔린다. 실제로 첫
    구현이 엔캐리 회차와 엔화 개입 주장을 못 잡았다.

    그렇다고 포함률만으로는 부족하다. 열 단어짜리 문장 둘이 흔한 말 두 개를
    공유하는 것과 고유한 말 두 개를 공유하는 것은 전혀 다른데 숫자는 같다.
    그래서 **겹친 말 자체를 함께 돌려준다.** "엔화, 엔캐리" 를 보면 사람이
    0.3초에 판단하고, "미국, 시장" 을 보면 무시한다. 문턱을 낮게 잡고
    판단을 사람에게 넘기는 편이 문턱을 정교하게 맞추려 애쓰는 것보다 낫다.

    자동으로 거르지 않는 이유도 같다. 겹치는 것이 반복인지 후속인지는
    사람만 안다. 같은 주제를 반년 뒤에 다시 보는 것은 좋은 일이고, 모르고
    하는 것만 문제다.
    """
    query = _tokens(text)
    if len(query) < MIN_QUERY_TOKENS:
        return []

    found = []
    for s in history.sessions:
        other = _tokens(s.text())
        if not other:
            continue
        shared = query & other
        score = len(shared) / len(query)
        if score >= floor:
            found.append(Match(s, score, sorted(shared, key=len, reverse=True)))

    found.sort(key=lambda m: -m.score)
    return found[:top]
