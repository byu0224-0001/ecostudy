"""뉴스레터 한 통을 블록으로 쪼갠다.

## 왜 계층을 하나 더 두는가

지금까지는 정제한 줄에서 곧바로 "숫자가 붙은 문장"을 뽑았다. 그 방식이
놓치는 것을 실측으로 확인했다. DeepSearch 8/10 메일은 토픽이 셋인데 주장은
하나만 건졌고, "미일 공동 개입" 같은 숫자 없는 인과 문장은 통째로 사라졌다.

줄은 너무 잘고 메일은 너무 크다. 그 사이에 **블록**이 필요하다. 블록은
제목 하나와 거기 딸린 문단들이고, 대략 사람이 "기사 하나"라고 부를 단위다.

## 무엇을 코드가 하고 무엇을 모델이 하는가

여기서는 자르기만 한다. 자르는 일은 HTML 구조를 따라가면 되므로 규칙으로
충분하고, 오히려 모델에게 시키면 매번 다르게 자른다.

이 블록이 무슨 성격인지(기사인지 광고인지 일정인지)는 `core/llm.py` 가
모델에게 묻는다. 그건 규칙으로 하면 매체마다 예외가 끝없이 늘어난다.

그리고 **권한은 모델이 정하지 않는다.** 아래 POLICY 표가 정한다. 모델이
분류를 틀리면 한 블록이 잘못 분류될 뿐이지만, 모델이 권한까지 정하면
"협찬 글을 증거로 쓰지 않는다"는 원칙 자체가 매번 흔들린다. 틀릴 수 있는
것과 틀리면 안 되는 것을 갈라놓는다.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field

# 블록의 성격. 모델이 이 중 하나를 고른다.
CONTENT_TYPES = (
    "lead_story",   # 그날의 주 기사
    "brief_news",   # 짧은 소식 여러 건
    "calendar",     # 예정된 일
    "sponsored",    # 협찬·광고
    "self_promo",   # 뉴스레터 자체 홍보, 이벤트, 오픈채팅
    "lifestyle",    # 투자와 무관한 읽을거리
    "footer",       # 수신거부, 사업자 정보
    "ui_residue",   # 버튼·구독 링크 등 UI 조각 (분류 전 제거)
    "unknown",
)

# 성격별로 무엇에 쓸 수 있는가. **모델이 아니라 여기가 정한다.**
#
# discover — 여기서 주제의 실마리를 찾아도 되는가
# evidence — 이 블록의 서술을 근거로 인용해도 되는가
#
# 협찬 글에 discover 를 허용한 것이 핵심이다. 광고는 대개 새로운 상품이나
# 산업 변화를 먼저 말하므로 실마리로는 값이 있다. 다만 파는 사람이 쓴 글이라
# 근거로는 절대 쓰지 않는다.
#
# 그리고 evidence 가 True 인 것도 '뉴스레터 안에서 상대적으로'라는 뜻이지
# 1차 소스라는 뜻이 아니다. 뉴스레터는 어떤 블록이든 통념층이고 check 를
# 거쳐야 한다.
POLICY: dict[str, tuple[bool, bool]] = {
    "lead_story": (True, True),
    "brief_news": (True, True),
    "calendar": (True, False),
    "sponsored": (True, False),
    "self_promo": (False, False),
    "lifestyle": (False, False),
    "footer": (False, False),
    "ui_residue": (False, False),
    "unknown": (True, False),
}

# 한 블록이 이보다 길면 모델에 넣기 부담스럽고 여러 기사가 섞였을 확률이
# 높다. 문단 경계에서 나눈다.
MAX_BLOCK_CHARS = 2400
MIN_BLOCK_CHARS = 24

TAG = re.compile(r"(?is)<(script|style|head)[^>]*>.*?</\1>")
BREAK = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</td>|</h[1-6]>|</li>")
ANCHOR = re.compile(r'(?is)<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>')
STRIP = re.compile(r"(?s)<[^>]+>")

# 제목 태그를 버리지 않는다. 처음에는 "짧은 줄이면 제목"으로 짐작했는데
# 그러면 "지금 가입하면 첫 달 무료! 신청하기" 같은 광고 버튼이 제목으로
# 잡히고 본문이 비어서 블록째로 버려졌다. 분류하려던 협찬 글이 분류되기
# 전에 사라진 것이다. 문서가 이미 알려주는 것을 짐작하고 있었다.
#
# 태그를 쓰는 것은 매체별 하드코딩이 아니다. h2 는 어느 뉴스레터에서나
# 제목이다. 반면 "class=article-title" 을 찾는 것은 매체별 하드코딩이다.
HEADING_OPEN = re.compile(r"(?i)<h[1-6]\b[^>]*>")
MARK = "\x01"

# 추적 픽셀과 수신거부 링크는 본문이 아니다.
JUNK_LINK = re.compile(
    r"unsubscribe|optout|opt-out|/track|/open\?|pixel|utm_|list-manage"
    r"|stibee\.com/api|beacon", re.I)


@dataclass
class Block:
    index: int
    heading: str
    body: str
    links: list[str] = field(default_factory=list)

    # 모델이 채운다.
    content_type: str = "unknown"
    reason: str = ""

    @property
    def discovery_allowed(self) -> bool:
        return POLICY.get(self.content_type, POLICY["unknown"])[0]

    @property
    def evidence_allowed(self) -> bool:
        return POLICY.get(self.content_type, POLICY["unknown"])[1]

    @property
    def text(self) -> str:
        return f"{self.heading}\n{self.body}".strip() if self.heading else self.body


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(STRIP.sub(" ", fragment))).strip()


@dataclass
class Row:
    text: str
    links: list[str]
    tagged: bool     # 진짜 제목 태그 안에 있었다


def _rows(html: str) -> list[Row]:
    """(줄, 링크, 제목태그 여부) 목록.

    링크를 버리지 않는 이유는 그것이 블록의 성격을 가장 잘 드러내기
    때문이다. 협찬 글에는 광고주 도메인이, 자체 홍보에는 자기 도메인이,
    바닥글에는 수신거부 링크가 붙는다.
    """
    body = HEADING_OPEN.sub(lambda m: f"\n{MARK}{m.group(0)}", TAG.sub(" ", html))

    hrefs: list[str] = []

    def keep(match: re.Match) -> str:
        url, inner = match.group(1), match.group(2)
        if JUNK_LINK.search(url):
            return inner
        hrefs.append(url)
        return f"\x00{len(hrefs) - 1}\x00{inner}"

    body = BREAK.sub("\n", ANCHOR.sub(keep, body))

    out: list[Row] = []
    for raw in body.split("\n"):
        marks = [int(m) for m in re.findall(r"\x00(\d+)\x00", raw)]
        line = _clean(re.sub(r"\x00\d+\x00", " ", raw).replace(MARK, ""))
        if line and line not in ("-->", "<!--"):
            out.append(Row(text=line, tagged=MARK in raw,
                           links=[hrefs[i] for i in marks if i < len(hrefs)]))
    return out


def _looks_like_body(line: str) -> bool:
    from .newsletters import NUMERIC, SENTENCE_END
    return bool(len(line) > 34 or NUMERIC.search(line) or SENTENCE_END.search(line))


def _is_heading(rows: list[Row], i: int) -> bool:
    """제목 태그면 제목이다. 아니면 짧고 **뒤에 본문이 따라오는** 줄만 제목이다.

    뒤를 보는 조건이 없으면 광고 버튼 문구나 바닥글 항목이 죄다 제목이 되고,
    본문 없는 빈 블록으로 남아 버려진다.
    """
    row = rows[i]
    if row.tagged:
        return True
    if _looks_like_body(row.text):
        return False
    if not (len(row.text) >= 4 or re.match(r"^[^\w\s]", row.text)):
        return False
    nxt = rows[i + 1] if i + 1 < len(rows) else None
    return bool(nxt and not nxt.tagged and _looks_like_body(nxt.text))


def split(html: str) -> list[Block]:
    """제목이 나올 때마다 새 블록을 연다."""
    rows = _rows(html)

    groups: list[tuple[str, list[str], list[str]]] = []
    heading, lines, links = "", [], []

    def flush():
        text = " ".join(lines).strip()
        # 본문이 짧아도 링크가 걸려 있으면 남긴다. 광고 버튼이 그렇게 생겼고,
        # 그게 바로 분류해야 할 대상이다.
        if len(text) + len(heading) >= MIN_BLOCK_CHARS or links:
            groups.append((heading, lines[:], links[:]))

    for i, row in enumerate(rows):
        if _is_heading(rows, i):
            flush()
            heading, lines, links = row.text, [], list(row.links)
        else:
            lines.append(row.text)
            links.extend(row.links)
    flush()

    blocks: list[Block] = []
    for head, body_lines, hrefs in groups:
        for chunk in _chunk(body_lines):
            blocks.append(Block(
                index=len(blocks),
                heading=re.sub(r"^[^\w가-힣]+|[^\w가-힣]+$", "", head).strip(),
                body=" ".join(chunk).strip(),
                links=list(dict.fromkeys(hrefs))[:6],
            ))
    return mark_ui_residue(blocks)


def _chunk(lines: list[str]) -> list[list[str]]:
    """너무 긴 묶음을 문단 경계에서 나눈다. 문장 중간에서는 자르지 않는다."""
    out, cur, size = [], [], 0
    for line in lines:
        if cur and size + len(line) > MAX_BLOCK_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += len(line)
    if cur:
        out.append(cur)
    return out or [[]]


# UI 조각 판별 — 보수적으로. 애매하면 남긴다.
UI_EXACT = frozenset({"답변 보기", "수신거부", ".", "…"})
UI_MARKERS = (
    "구독하기", "수신거부", "광고문의", "잘림 없이", "구독 이메일",
    "알림 설정", "이메일 수신을 원하지",
)


def is_ui_residue(block: Block) -> bool:
    """고확신 UI 만 걸러낸다. 애매하면 남긴다."""
    text = block.text.strip()
    if not text or text in UI_EXACT:
        return True
    if text.startswith("답변 보기") and len(text) < 220:
        return True
    if len(text) <= 8 and not block.links:
        return text in UI_EXACT or text in ("·", "-")

    has_news = bool(re.search(
        rf"\d[\d,.]*\s*(?:%|％|원|엔|달러|포인트)", text
    ))
    if has_news and len(text) > 35:
        return False

    if "수신거부" in text and "Copyright" in text and not has_news:
        return True
    if text.startswith("NEWNEEK |") and "구독하기" in text and not has_news:
        return True

    ui_hits = sum(1 for m in UI_MARKERS if m in text)
    if ui_hits == 0:
        return False

    if not has_news and len(text) < 28 and ui_hits >= 1:
        if text.replace("|", " ").strip() in (
            "구독하기", "답변 보기", "수신거부",
        ):
            return True
        if ui_hits >= 2 or (len(text) < 18 and ui_hits >= 1):
            return True
    return False


def mark_ui_residue(blocks: list[Block]) -> list[Block]:
    """UI 조각에 ui_residue 를 붙인다. 삭제하지 않고 provenance 를 남긴다."""
    for b in blocks:
        if b.content_type == "unknown" and is_ui_residue(b):
            b.content_type = "ui_residue"
            b.reason = "UI 조각 (분류 전 deterministic filter)"
    return blocks


def usable_blocks(blocks: list[Block]) -> list[Block]:
    """분류·추출 대상. ui_residue 는 제외."""
    return [b for b in blocks if b.content_type != "ui_residue"]


def render(blocks: list[Block], *, max_chars: int = 700) -> str:
    """모델에게 보낼 형태.

    HTML 을 그대로 던지지 않는 이유는 뉴스레터 한 통이 47,000 토큰인데
    정제하면 5,000 토큰으로 줄기 때문이다. 표와 스타일과 추적 코드에
    돈을 쓸 이유가 없다.
    """
    parts = []
    for b in blocks:
        if b.content_type == "ui_residue":
            continue
        head = f"제목: {b.heading}\n" if b.heading else ""
        body = b.body[:max_chars]
        link = f"\n링크: {', '.join(b.links[:3])}" if b.links else ""
        parts.append(f"[{b.index}]\n{head}{body}{link}")
    return "\n\n".join(parts)
