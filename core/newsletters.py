"""뉴스레터 메일에서 검증할 주장을 뽑는다.

## 뉴스레터는 1차 소스가 아니다

이 모듈이 절대 하지 않는 일이 있다. **무엇도 사실로 표시하지 않는다.**
여기서 나오는 모든 항목은 `통념` 아니면 `견해` 층이고, 대조를 거쳐야
한다. 잘 정리된 글일수록 그대로 믿기 쉬워서 이 경계가 더 중요하다.

실제 사례가 있다. UPPITY 가 "미일 공동 개입으로 155엔대까지 내려갔다"고
썼는데 ECOS 일별 매매기준율의 저점은 157.32 였다. 장중 저가와 일별
기준율의 차이로 보이지만, 어느 쪽이든 숫자를 그대로 옮겼으면 틀린 수를
자료에 실었을 것이다.

반대로 뉴스레터만 줄 수 있는 것도 분명하다. 같은 건에서 "미일 공동
개입"이라는 **인과**는 가격 데이터로는 절대 알 수 없다. 그래서 버리지
않고, 대조 대상으로 받는다.

## 무엇을 뽑는가

숫자가 붙은 문장만 고른다. "시장이 불안하다"는 대조할 수 없고 "코스닥이
8월 들어 18.72% 올랐다"는 대조할 수 있다. 반증 가능성이 곧 필터다.

일정은 예외로 따로 뽑는다. 실적 발표일·공모주 청약·지표 발표일은 어떤
어댑터로도 얻기 어려운데 뉴스레터에는 매일 정리되어 온다. 이게 이 경로의
가장 큰 값이다.
"""

from __future__ import annotations

import email
import html as html_module
import re
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.header import decode_header, make_header
from pathlib import Path

from . import claims as claims_mod

# 발신 주소 → 어떤 성격의 글인가. 층 구분은 topics/sources.yaml 과 같다.
PROFILES = {
    "mydailybyte.com": {"name": "DAILY_BYTE", "layer": "통념"},
    "uppity.co.kr": {"name": "UPPITY", "layer": "통념"},
    "newneek.co": {"name": "NEWNEEK", "layer": "통념"},
    # 사람이 쓴 글이 아니라 LLM 이 뉴스를 요약하고 '인사이트'를 붙인 것이다.
    # 본문 말미에 정확성을 보장하지 않는다고 스스로 밝힌다. 요약의 사실
    # 부분은 쓸 만하지만 인사이트는 검증 대상 그 자체다.
    "deepsearch.com": {"name": "DeepSearch", "layer": "통념", "generated": True},
}

# 대조 가능한 문장의 표지. 숫자와 단위가 함께 있어야 한다.
UNITS = r"%|％|원|엔|달러|위안|조|억|만|bp|포인트|배|건|명|년|월|일|시간|차례|번"
NUMERIC = re.compile(rf"\d[\d,.]*\s*(?:{UNITS})")

# 일정은 따로 뽑는다. 어떤 어댑터로도 얻기 어려운데 매일 정리되어 온다.
SCHEDULE_MARKERS = ("일정", "📆", "이번 주", "오늘의 일정")
SCHEDULE_HINTS = re.compile(
    r"발표|공모|청약|상장|실적|배당|만기|회의|결정|시행|개막|마감|공시")

# 본문이 아닌 것. 광고·수신거부·소셜 링크가 숫자를 달고 오는 일이 잦다.
NOISE = re.compile(
    r"수신거부|구독하기|광고 문의|무단전재|저작권|Copyright|모바일로 발송"
    r"|오픈채팅|카카오톡 알림|앱에서 보기|새 창에서 보기|잘림 없이"
    r"|당첨자|퀴즈|이벤트|입장코드|고객문의|문의하기|이메일 수신"
    r"|보장되지 않을 수 있습니다|언어 모델을 사용")

MIN_LEN = 18
MAX_LEN = 400


@dataclass
class Item:
    text: str
    kind: str          # claim | schedule
    section: str

    # 아래는 모델 경로에서만 채워진다.
    headline: str = ""
    interpretation: str = ""
    claim_type: str = ""
    source_span: str = ""
    provenance: str = "none"      # exact | stitched | fuzzy | missing | none
    verification_mode: str = ""
    verify: str = ""              # capture CLI 호환 (adapter 목록)
    adapter_verifiable: bool = False
    block: int | None = None
    evidence_allowed: bool = True

    @property
    def quotable(self) -> bool:
        """원문 그대로 인용해도 되는가."""
        return claims_mod.quotable(self.provenance)


@dataclass
class Newsletter:
    sender: str
    name: str
    layer: str
    subject: str
    date: datetime | None
    path: Path | None = None
    generated: bool = False
    items: list[Item] = field(default_factory=list)
    blocks: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    classified: bool = False
    extracted: bool = False

    @property
    def claims(self) -> list[Item]:
        return [i for i in self.items if i.kind == "claim"]

    @property
    def schedule(self) -> list[Item]:
        return [i for i in self.items if i.kind == "schedule"]


def _decode(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return raw.strip()


def _profile(sender: str) -> dict:
    low = sender.lower()
    for domain, profile in PROFILES.items():
        if domain in low:
            return profile
    return {"name": sender.split("<")[0].strip() or sender, "layer": "통념"}


def to_lines(html: str) -> list[str]:
    """HTML 메일에서 읽을 수 있는 줄만 남긴다.

    뉴스레터는 표 기반 마케팅 템플릿이라 태그가 본문의 스무 배쯤 된다.
    라이브러리를 더 붙이지 않는 이유는 필요한 것이 문단 구분과 텍스트뿐이기
    때문이다.
    """
    body = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</td>|</h[1-6]>|</li>",
                  "\n", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    out = []
    for line in html_module.unescape(body).split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line and line not in ("-->", "<!--"):
            out.append(line)
    return out


# 한국어 평서문의 끝. 제목은 이렇게 끝나지 않는다.
SENTENCE_END = re.compile(r"(요|다|죠|니다)[.!]?$")


def _section_of(line: str) -> str | None:
    """섹션 제목처럼 보이는 줄.

    짧고, 숫자가 없고, 문장으로 끝나지 않는다. 이모지로 시작하는 것만
    보다가 DAILY_BYTE 의 "코스닥, 어떻게 이렇게 올랐지" 같은 맨 텍스트
    제목을 통째로 놓쳤다.
    """
    if len(line) > 30 or NUMERIC.search(line):
        return None
    if re.match(r"^[^\w\s]", line) or "|" in line:
        return re.sub(r"^[^\w가-힣]+|[^\w가-힣]+$", "", line).strip() or None
    if not SENTENCE_END.search(line) and len(line) >= 4:
        return line.strip()
    return None


def extract(lines: list[str]) -> list[Item]:
    items: list[Item] = []
    section = ""
    in_schedule = False
    seen: set[str] = set()

    for line in lines:
        if heading := _section_of(line):
            section = heading
            in_schedule = any(m in line for m in SCHEDULE_MARKERS)
            continue

        if NOISE.search(line) or not (MIN_LEN <= len(line) <= MAX_LEN):
            continue

        if in_schedule and SCHEDULE_HINTS.search(line):
            kind = "schedule"
        elif NUMERIC.search(line):
            kind = "claim"
        else:
            continue

        key = re.sub(r"\W", "", line)[:60]
        if key in seen:
            continue
        seen.add(key)
        items.append(Item(text=line, kind=kind, section=section))

    return items


def _html_of(msg) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_content()
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            return part.get_content()
    return ""


def enrich(letter: Newsletter, *, refresh: bool = False) -> Newsletter:
    """블록을 분류하고 주장을 다시 뽑는다.

    정규식 경로는 "숫자와 단위가 붙은 줄"만 남긴다. 그래서 "미일 공동
    개입으로 되돌려졌다" 같은 인과 문장을 놓치고, DeepSearch 한 통에서
    토픽 셋 중 하나만 건졌다. 여기서 그걸 고친다.

    실패는 블록 단위로 격리한다. 한 블록이 이상해서 하루치를 버리는 일은
    없어야 한다.
    """
    from . import blocks as blocks_mod
    from . import llm

    if not letter.blocks:
        return letter

    # ui_residue 는 split() 에서 이미 표시됨. 분류 대상만 render.
    classifiable = blocks_mod.usable_blocks(letter.blocks)
    if not classifiable:
        letter.notes.append("분류할 블록 없음 (전부 UI 조각)")
        return letter

    # 1) 분류
    try:
        got = llm.ask("classify_blocks",
                      blocks_mod.render(classifiable),
                      llm.BlockLabels, refresh=refresh)
        by_index = {b.index: b for b in letter.blocks}
        for row in got.data.get("labels", []):
            block = by_index.get(row.get("index"))
            if block and row.get("content_type") in blocks_mod.CONTENT_TYPES:
                block.content_type = row["content_type"]
                block.reason = row.get("reason", "")
        letter.classified = True
    except Exception as exc:
        letter.notes.append(f"분류 실패: {exc}")
        return letter

    # 2) 추출
    usable = [b for b in letter.blocks
              if b.content_type != "ui_residue" and b.discovery_allowed]
    found: list[Item] = []
    for block in usable:
        if len(block.text) < MIN_LEN:
            continue
        try:
            got = llm.ask("extract_claims", block.text, llm.Claims,
                          refresh=refresh)
        except Exception as exc:
            letter.notes.append(f"블록 {block.index} 추출 실패: {exc}")
            continue
        for row in got.data.get("claims", []):
            claim_type = claims_mod.normalize_type(row.get("claim_type"))
            ver = claims_mod.verification_from_dict(
                row.get("verification"), claim_type=claim_type)
            source_span = (row.get("source_span") or "").strip()

            # 모델 말을 믿지 않고 원문에서 직접 찾는다. 호출도 비용도 없다.
            provenance = claims_mod.check_provenance(source_span, block.text)
            if provenance in ("missing", "fuzzy"):
                letter.notes.append(
                    f"블록 {block.index}: source_span {provenance} — "
                    f"{source_span[:40]}")

            found.append(Item(
                text=row.get("claim", "").strip(),
                kind="schedule" if block.content_type == "calendar" else "claim",
                section=block.heading,
                headline=row.get("headline", "").strip(),
                interpretation=row.get("interpretation", "").strip(),
                claim_type=claim_type,
                source_span=source_span,
                provenance=provenance,
                verification_mode=ver.mode,
                verify=claims_mod.adapters_of(ver),
                adapter_verifiable=ver.adapter_verifiable,
                block=block.index,
                evidence_allowed=block.evidence_allowed,
            ))

    if found:
        letter.items = [i for i in found if i.text]
        letter.extracted = True
    return letter


def parse(path: Path) -> Newsletter:
    with path.open("rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)

    sender = _decode(msg.get("From"))
    profile = _profile(sender)

    html = ""
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_content()
            break
    if not html:
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                html = part.get_content()
                break

    try:
        when = email.utils.parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        when = None

    from . import blocks as blocks_mod

    return Newsletter(
        sender=sender,
        name=profile["name"],
        layer=profile["layer"],
        generated=profile.get("generated", False),
        subject=_decode(msg.get("Subject")),
        date=when,
        path=path,
        items=extract(to_lines(html)),
        blocks=blocks_mod.split(html),
    )


def read_dir(directory: Path, *, since: datetime | None = None,
             enhance: bool = False, refresh: bool = False) -> list[Newsletter]:
    out = []
    for path in sorted(directory.glob("*.eml")):
        try:
            letter = parse(path)
        except (OSError, ValueError):
            continue
        if since and letter.date and letter.date < since:
            continue
        if enhance:
            enrich(letter, refresh=refresh)
        out.append(letter)
    return sorted(out, key=lambda n: n.date or datetime.min.replace(
        tzinfo=None if not n.date else n.date.tzinfo), reverse=True)


# feeds.STOPWORDS 는 제목용으로 골랐다. 뉴스레터는 산문이라 연결어와
# 서술어가 훨씬 많이 섞여 들어온다. 처음 돌렸을 때 세 매체가 겹친 단어가
# "가장·결국·다만·만에"뿐이었던 게 그 증거다.
PROSE_STOPWORDS = {
    "가장", "결국", "다른", "다만", "만에", "같은", "것으로", "것이", "것은",
    "이상", "이하", "꾸준히", "내용이", "넘게", "다음", "주요", "기준", "규모",
    "통해", "위한", "위해", "따라", "대한", "관련", "경우", "대비", "이후",
    "전망", "분석", "예상", "가능성", "상황", "수준", "부분", "때문", "정도",
    "기업의", "시장의", "정부의", "국내", "해외", "지난", "현재", "향후",
    "그러나", "하지만", "그리고", "또한", "특히", "실제", "사실", "먼저",
    "이런", "그런", "저런", "어떤", "무슨", "이렇게", "그렇게", "라고",
    "된다", "한다", "했다", "있다", "없다", "본다", "나온다", "밝혔다",
    "기록", "발표", "확대", "축소", "증가", "감소", "상승", "하락",
}

# 어느 날에나 나오는 말은 그날의 이야기가 아니다. 관측 일수가 이만큼은
# 쌓여야 이 판단이 의미가 있다.
UBIQUITY_MIN_DAYS = 3
UBIQUITY_RATIO = 0.6


def _words(letter: Newsletter) -> set[str]:
    """조사를 떼고 센다.

    떼지 않으면 "가격·가격이·가격을·가격에"가 서로 다른 네 단어가 되어
    상위권을 혼자 차지한다. 회차 유사도에서 쓰는 것과 같은 처리다.
    """
    from .feeds import STOPWORDS, TOKEN
    from .sessions import _stem

    out = set()
    for item in letter.claims:
        for token in TOKEN.findall(item.text):
            word = token.lower() if token.isascii() else _stem(token)
            if len(word) >= 2 and word not in STOPWORDS \
                    and word not in PROSE_STOPWORDS:
                out.add(word)
    return out


def _day(letter: Newsletter) -> str:
    return letter.date.strftime("%Y-%m-%d") if letter.date else "?"


def signals(letters: list[Newsletter], *, day: str | None = None,
            min_sources: int = 2, top: int = 12
            ) -> list[tuple[str, list[str]]]:
    """오늘 여러 곳이 함께 말하지만, 늘 하는 말은 아닌 것.

    피드의 지속성과 같은 발상이다. 한 곳이 열 번 말하는 것은 그 매체의 편집
    방향이고, 서로 다른 곳이 같은 날 겹치면 그날의 통념이다. 다만 겹침만
    보면 "주요·기준·규모"처럼 매일 나오는 말이 위로 올라온다. 그래서 관측된
    날의 대부분에 등장하는 단어는 배경으로 보고 뺀다.

    불용어 목록으로 다 막지 않는 이유가 있다. 목록은 손으로 채워야 하고 늘
    뒤처지지만, 이 방식은 메일이 쌓일수록 저절로 정확해진다.
    """
    days = {_day(letter) for letter in letters}
    target = day or (max(days) if days else "?")

    seen_days: dict[str, set[str]] = {}
    for letter in letters:
        for word in _words(letter):
            seen_days.setdefault(word, set()).add(_day(letter))

    background = set()
    if len(days) >= UBIQUITY_MIN_DAYS:
        background = {word for word, on in seen_days.items()
                      if len(on) / len(days) > UBIQUITY_RATIO}

    where: dict[str, set[str]] = {}
    for letter in letters:
        if _day(letter) != target:
            continue
        for word in _words(letter) - background:
            where.setdefault(word, set()).add(letter.name)

    # 같은 수의 매체가 다뤘다면 평소 안 나오던 말이 먼저다. 가나다순으로
    # 자르면 "가격"류가 위를 채우고 그날 새로 생긴 이야기가 밀려난다.
    rows = [(word, sorted(names)) for word, names in where.items()
            if len(names) >= min_sources]
    rows.sort(key=lambda row: (-len(row[1]),
                               len(seen_days.get(row[0], ())), row[0]))
    return rows[:top]
