"""주제 후보 풀 로더와 규칙 검사기.

`docs/TOPICS.md`의 규칙을 문서에만 두면 반년 뒤에는 지켜지지 않는다.
여기서 검사하는 것은 취향이 아니라 이 모임의 형식 계약이다.

가장 중요한 검사는 **결론 필드 거부**다. 후보 단계에서 답을 적기 시작하면
세션은 토론이 아니라 발표가 된다. 그 경계는 사람의 기억이 아니라 코드가
지켜야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config

BACKLOG = config.ROOT / "topics" / "backlog.yaml"
INBOX = config.ROOT / "topics" / "inbox.yaml"

AXES = {
    1: "시장 국면",
    2: "산업·섹터 비교",
    3: "기업 분석",
    4: "포트폴리오·리스크",
    5: "거래·의사결정 복기",
    6: "투자 성향·스타일",
}
JUDGMENTS = {"국면", "비교", "가치", "리스크", "타이밍", "성향"}
READINESS = {"full", "partial", "external"}
STATUSES = {"candidate", "selected", "done", "dropped"}

# A candidate states a question and the data that would narrow it. Anything
# that states an answer belongs to the session, not the backlog.
BANNED_FIELDS = {
    "conclusion", "answer", "verdict", "tldr", "summary", "view",
    "recommendation", "takeaway", "결론", "요약", "정답", "전망",
}

# Personal-axis topics have one honest split; everything else needs a real
# disagreement to be worth a session.
PERSONAL_SPLIT = "개인별로 갈린다"

REQUIRED = ("id", "axis", "judgment", "title", "question",
            "why_now", "splits", "data", "output", "status")


@dataclass
class Issue:
    topic: str
    message: str


@dataclass
class Backlog:
    topics: list[dict] = field(default_factory=list)
    books: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def all(self) -> list[dict]:
        return [*self.topics, *self.books]

    def get(self, topic_id: str) -> dict | None:
        wanted = topic_id.upper()
        return next((t for t in self.all if str(t.get("id", "")).upper() == wanted), None)

    def select(self, *, axis: int | None = None, ready: str | None = None,
               status: str | None = None, books: bool | None = None) -> list[dict]:
        rows = self.all
        if books is True:
            rows = self.books
        elif books is False:
            rows = self.topics
        if axis is not None:
            rows = [t for t in rows if t.get("axis") == axis]
        if ready:
            rows = [t for t in rows if (t.get("data") or {}).get("ready") == ready]
        if status:
            rows = [t for t in rows if t.get("status") == status]
        return rows


CHECK_RESULTS = {"matches", "contradicts", "unverifiable"}

# What a captured item must carry. The claim is the point: a headline is a
# frame, a claim is something the data can disagree with.
CAPTURE_REQUIRED = ("id", "headline", "claim", "source", "seen", "origin")


@dataclass
class Inbox:
    """포착만 되고 아직 정식 후보가 아닌 것들.

    여기 있는 항목은 축도 splits 도 없다. 그것을 채우는 일이 승격이고,
    승격 전에 지표 대조를 통과해야 한다.
    """

    items: list[dict] = field(default_factory=list)

    def get(self, item_id: str) -> dict | None:
        wanted = item_id.upper()
        return next((i for i in self.items
                     if str(i.get("id", "")).upper() == wanted), None)

    def pending(self) -> list[dict]:
        return [i for i in self.items if not i.get("check")]

    def next_id(self) -> str:
        used = [int(str(i.get("id", "N-0"))[2:]) for i in self.items
                if str(i.get("id", "")).upper().startswith("N-")
                and str(i.get("id", ""))[2:].isdigit()]
        return f"N-{max(used, default=0) + 1:03d}"


def load_inbox(path: Path | None = None) -> Inbox:
    src = path or INBOX
    if not src.exists():
        return Inbox()
    raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    return Inbox(items=raw.get("captured") or [])


def save_inbox(inbox: Inbox, path: Path | None = None) -> Path:
    dst = path or INBOX
    dst.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"captured": inbox.items},
        allow_unicode=True, sort_keys=False, width=88,
    )
    header = (
        "# 포착함 — 읽다가 걸린 것을 그 자리에서 던져 넣는 곳.\n"
        "#\n"
        "# 여기 적는 것은 기사 요약이 아니라 기사가 하는 '검증 가능한 주장' 하나다.\n"
        "# verify 를 채울 수 없으면 그 자리에서 버린다. 규칙은 docs/TOPICS.md.\n\n"
    )
    dst.write_text(header + body, encoding="utf-8")
    return dst


def validate_inbox(inbox: Inbox) -> list[Issue]:
    out: list[Issue] = []
    for item in inbox.items:
        iid = str(item.get("id", "?"))
        for f in CAPTURE_REQUIRED:
            if not item.get(f):
                out.append(Issue(iid, f"필수 필드 없음: {f}"))
        if (c := item.get("check")) and c not in CHECK_RESULTS:
            out.append(Issue(iid, f"check 는 {sorted(CHECK_RESULTS)} 중 하나 (현재 {c!r})"))
        # An unfalsifiable claim fails 탈락 조건 2 before anyone spends time on it
        if not item.get("verify"):
            out.append(Issue(iid, "verify 가 비어 있다 — 데이터로 확인할 방법이 없으면 주제가 아니다"))
        if item.get("check") == "unverifiable" and item.get("status") != "dropped":
            out.append(Issue(iid, "확인 불가로 판정됐으면 status 를 dropped 로 내린다"))
    return out


def load(path: Path | None = None) -> Backlog:
    src = path or BACKLOG
    if not src.exists():
        raise FileNotFoundError(f"후보 풀이 없습니다: {src}")
    raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    return Backlog(
        topics=raw.get("topics") or [],
        books=raw.get("books") or [],
        meta=raw.get("meta") or {},
    )


def _check_topic(t: dict) -> list[Issue]:
    tid = str(t.get("id", "?"))
    out: list[Issue] = []

    for f in REQUIRED:
        if not t.get(f):
            out.append(Issue(tid, f"필수 필드 없음: {f}"))

    for f in t:
        if f.lower() in BANNED_FIELDS:
            out.append(Issue(tid, f"결론 필드는 후보에 넣지 않는다: {f}"))

    axis = t.get("axis")
    if axis not in AXES:
        out.append(Issue(tid, f"axis 는 1~6 이어야 한다 (현재 {axis!r})"))

    if (j := t.get("judgment")) and j not in JUDGMENTS:
        out.append(Issue(tid, f"judgment 는 {sorted(JUDGMENTS)} 중 하나 (현재 {j!r})"))

    if (s := t.get("status")) and s not in STATUSES:
        out.append(Issue(tid, f"status 는 {sorted(STATUSES)} 중 하나 (현재 {s!r})"))

    # Korean interrogatives usually end in 가/나/까 and may carry a period
    # rather than a question mark, so strip punctuation before checking.
    question = str(t.get("question", "")).strip().rstrip(".?？。！! ")
    if question and not question.endswith(("가", "나", "까", "지", "요")):
        out.append(Issue(tid, "question 이 질문 형태로 끝나지 않는다"))

    # every why_now item must be traceable, or it is just an impression
    for i, w in enumerate(t.get("why_now") or []):
        if isinstance(w, str):
            out.append(Issue(tid, f"why_now[{i}] 에 출처가 없다"))
        elif not w.get("source"):
            out.append(Issue(tid, f"why_now[{i}] 에 source 가 없다"))

    splits = t.get("splits") or []
    personal = splits == [PERSONAL_SPLIT]
    if not personal and len(splits) < 2:
        out.append(Issue(tid, "splits 가 2개 미만이다 — 찬반이 갈리지 않으면 토론이 아니다"))

    data = t.get("data") or {}
    ready = data.get("ready")
    if ready not in READINESS:
        out.append(Issue(tid, f"data.ready 는 {sorted(READINESS)} 중 하나 (현재 {ready!r})"))
    if ready in ("full", "partial") and not data.get("series"):
        out.append(Issue(tid, f"data.ready={ready} 인데 series 가 비어 있다"))
    if ready == "partial" and not (data.get("missing") or data.get("note")
                                   or data.get("caveat")):
        out.append(Issue(tid, "partial 이면 무엇이 부족한지 missing/note/caveat 에 적는다"))

    # "각자 알아서" 로 두면 운영자가 빈손으로 나간다. 준비물이 없다는 것도
    # 결정이므로 적어야 한다.
    if personal and not data.get("note"):
        out.append(Issue(tid, "개인 축인데 운영자가 무엇을 준비하는지 적혀 있지 않다"))

    return out


def _check_book(b: dict) -> list[Issue]:
    bid = str(b.get("id", "?"))
    out: list[Issue] = []
    for f in ("id", "title", "axis", "question", "why_now", "output", "status"):
        if not b.get(f):
            out.append(Issue(bid, f"필수 필드 없음: {f}"))
    for f in b:
        if f.lower() in BANNED_FIELDS:
            out.append(Issue(bid, f"결론 필드는 후보에 넣지 않는다: {f}"))
    if b.get("axis") not in AXES:
        out.append(Issue(bid, f"axis 는 1~6 이어야 한다 (현재 {b.get('axis')!r})"))
    return out


def validate(backlog: Backlog) -> list[Issue]:
    issues: list[Issue] = []

    seen: dict[str, int] = {}
    for t in backlog.all:
        tid = str(t.get("id", "?"))
        seen[tid] = seen.get(tid, 0) + 1
    for tid, n in seen.items():
        if n > 1:
            issues.append(Issue(tid, f"id 가 {n}번 중복된다"))

    for t in backlog.topics:
        issues.extend(_check_topic(t))
    for b in backlog.books:
        issues.extend(_check_book(b))

    # A pool that only covers the macro axis will keep producing macro
    # sessions no matter how carefully each one is chosen.
    covered = {t.get("axis") for t in backlog.topics
               if t.get("status") == "candidate"}
    for axis in AXES:
        if axis not in covered:
            issues.append(Issue("풀 전체", f"{axis}축({AXES[axis]}) 후보가 없다"))

    return issues


def coverage(backlog: Backlog) -> dict[int, int]:
    out = {a: 0 for a in AXES}
    for t in backlog.topics:
        if t.get("status") == "candidate" and t.get("axis") in out:
            out[t["axis"]] += 1
    return out
