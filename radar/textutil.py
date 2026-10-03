import html
import re

STANCE_UP = ("상승", "인상", "매도", "부담", "급등")
STANCE_DOWN = ("하락", "인하", "매수", "둔화", "급락")
STANCE_RANGE = ("박스", "횡보", "레인지", "범위")

TOPICS = (
    ("fiscal", ("재정", "적자", "발행", "국채 공급"), "재정과 국채 공급", "국채 공급이 장기금리의 주된 압력인가"),
    ("inflation", ("물가", "인플레", "인플레이션", "cpi", "CPI"), "인플레이션", "물가가 다시 붙는 중인가"),
    ("growth", ("고용", "침체", "둔화", "수요"), "경기", "수요 둔화가 금리 압력을 흡수하는가"),
    ("term", ("텀 프리미엄", "기간 프리미엄", "term premium"), "텀 프리미엄", "레벨의 설명은 기간 프리미엄인가"),
    ("policy", ("연준", "기준금리", "FOMC", "연방준비"), "통화정책", "기준금리 경로가 장기금리를 끄는가"),
    ("spillover", ("유럽", "아시아로", "국채 매도"), "전이", "미국 국채 매도가 다른 시장으로 번지는가"),
)

TOPIC_WORDS = tuple(word for _, words, _, _ in TOPICS for word in words)
STANCE_WORDS = STANCE_UP + STANCE_DOWN + STANCE_RANGE

STANCE_LABEL = {
    "up": "상승 압력",
    "down": "하락 압력",
    "range": "범위",
    "structural": "구조",
    "unknown": "미분류",
}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def quote_status(quote: str, source: str) -> str:
    if not quote or not source:
        return "missing"
    if quote in source:
        return "exact"
    if compact(quote) and compact(quote) in compact(source):
        return "fuzzy"
    return "missing"


def keep_grounded(claims: list[dict], source: str) -> list[dict]:
    kept = []
    for claim in claims:
        quote = claim.get("quote") or ""
        status = quote_status(quote, source)
        if status == "missing":
            continue
        copied = dict(claim)
        copied["quote_status"] = status
        if status == "fuzzy":
            copied["quote"] = aligned_quote(quote, source)
        kept.append(copied)
    return kept


def aligned_quote(quote: str, source: str) -> str:
    folded = compact(quote)
    if not folded:
        return quote
    for sentence in split_sentences(source):
        folded_sentence = compact(sentence)
        if folded in folded_sentence or folded_sentence in folded:
            return sentence
    return quote


def split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[\.?!。])\s+", cleaned)
    refined = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= 180:
            refined.append(part)
            continue
        for piece in re.split(r"(?<=[,，])\s*", part):
            piece = piece.strip()
            if piece:
                refined.append(piece)
    return refined


def stance_of(text: str) -> str:
    up = sum(word in text for word in STANCE_UP)
    down = sum(word in text for word in STANCE_DOWN)
    ranged = sum(word in text for word in STANCE_RANGE)
    if ranged and not up and not down:
        return "range"
    if up and down:
        return "range"
    if up:
        return "up"
    if down:
        return "down"
    if "프리미엄" in text:
        return "structural"
    return "unknown"


def keywords_in(text: str) -> list[str]:
    found = []
    for word in TOPIC_WORDS + STANCE_WORDS:
        if word.lower() in text.lower() and word not in found:
            found.append(word)
    return found[:4]


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()
