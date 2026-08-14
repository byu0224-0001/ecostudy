"""소스 피드 수집 — 통념·견해·1차 층으로 나눠 읽는다.

이 모듈이 아는 것은 **피드를 어떻게 읽는지**뿐이고, 어떤 채널을 볼지는
`topics/sources.yaml` 이 정한다. `core/sources` 가 어떤 통계가 중요한지
모르는 것과 같은 규율이다.

여기서 나오는 것은 후보가 아니라 재료다. 제목을 훑고 검증 가능한 주장을
뽑아 `topics capture` 하는 일은 사람이 한다. 자동으로 후보를 만들면
결국 많이 나온 것이 올라오는데, 그게 정확히 피하려던 것이다.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml

from . import config
from .http import Client, FetchError

SOURCES = config.ROOT / "topics" / "sources.yaml"
STORE = config.ROOT / "topics" / "seen.jsonl"

ATOM = "{http://www.w3.org/2005/Atom}"
YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
YT_PAGE = "https://www.youtube.com/@{}"

# 채널 페이지에는 추천 채널의 channelId 가 잔뜩 들어 있다. externalId 만이
# 그 페이지 자신의 채널을 가리킨다.
EXTERNAL_ID = re.compile(r'"externalId":"(UC[\w-]{22})"')
CANONICAL_ID = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"')

# 데이터 API 가 아니라 공개 웹페이지·피드를 받으므로 일반 브라우저로 신원을
# 밝힌다. 기본 UA 로는 유튜브 채널 페이지가 다른 응답을 준다.
_client = Client(
    "feeds",
    min_interval=0.4,
    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"},
)


@dataclass
class Item:
    feed: str
    name: str
    layer: str
    title: str
    url: str
    published: datetime | None

    @property
    def age_days(self) -> float:
        if not self.published:
            return 999.0
        return (datetime.now(timezone.utc) - self.published).total_seconds() / 86400


def load_sources(path: Path | None = None) -> dict:
    src = path or SOURCES
    if not src.exists():
        raise FileNotFoundError(f"소스 목록이 없습니다: {src}")
    return yaml.safe_load(src.read_text(encoding="utf-8")) or {}


def _save_sources(data: dict, path: Path | None = None) -> None:
    (path or SOURCES).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=88),
        encoding="utf-8",
    )


def resolve_handle(handle: str) -> str:
    """유튜브 핸들 → 채널 ID. 1.7MB 페이지를 받으므로 결과는 저장해 재사용한다."""
    resp = _client.get(YT_PAGE.format(handle))
    for pattern in (EXTERNAL_ID, CANONICAL_ID):
        if m := pattern.search(resp.text):
            return m.group(1)
    raise FetchError(f"@{handle}: 채널 ID를 찾을 수 없습니다. 핸들을 확인하세요")


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _parse(body: str, feed: dict) -> list[Item]:
    """Atom 과 RSS 2.0 을 함께 처리한다. 유튜브·GeekNews 는 Atom, 국내 언론은 RSS."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise FetchError(f"{feed['id']}: 피드를 파싱할 수 없습니다 — {exc}") from exc

    out: list[Item] = []
    common = {"feed": feed["id"], "name": feed.get("name", feed["id"]),
              "layer": feed.get("layer", "?")}

    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        out.append(Item(
            title=(entry.findtext(f"{ATOM}title") or "").strip(),
            url=(link.get("href") if link is not None else "") or "",
            published=_parse_time(entry.findtext(f"{ATOM}published")
                                  or entry.findtext(f"{ATOM}updated")),
            **common,
        ))

    for item in root.iter("item"):
        out.append(Item(
            title=(item.findtext("title") or "").strip(),
            url=(item.findtext("link") or "").strip(),
            published=_parse_time(item.findtext("pubDate")),
            **common,
        ))

    return [i for i in out if i.title]


def fetch(feed: dict, *, sources: dict | None = None) -> list[Item]:
    if feed.get("kind") == "youtube":
        channel_id = feed.get("channel_id")
        if not channel_id:
            channel_id = resolve_handle(feed["handle"])
            feed["channel_id"] = channel_id
            if sources is not None:
                _save_sources(sources)
        url = YT_FEED.format(channel_id)
    else:
        url = feed["url"]

    return _parse(_client.get(url).text, feed)


def collect(*, layer: str | None = None, days: int = 7,
            only: list[str] | None = None) -> tuple[list[Item], list[str]]:
    """모든 피드를 훑는다. 하나가 죽어도 나머지는 돌아야 한다."""
    sources = load_sources()
    feeds = sources.get("feeds") or []
    if layer:
        feeds = [f for f in feeds if f.get("layer") == layer]
    if only:
        feeds = [f for f in feeds if f["id"] in only]

    items: list[Item] = []
    errors: list[str] = []
    for feed in feeds:
        try:
            items.extend(fetch(feed, sources=sources))
        except (FetchError, KeyError) as exc:
            errors.append(f"{feed.get('id', '?')}: {exc}")

    fresh = [i for i in items if i.age_days <= days]
    fresh.sort(key=lambda i: i.published or datetime.min.replace(tzinfo=timezone.utc),
               reverse=True)
    return fresh, errors


# --- 누적 -------------------------------------------------------------------
#
# RSS 는 최근 것만 돌려준다. 측정해 보면 연합뉴스 120건이 전부 하루도 안 된
# 것이고 삼프로도 15건이 하루치다. 그래서 "지난 30일 동안 반복됐는가"는
# 피드를 다시 읽어서는 절대 알 수 없고, 매일 받아 쌓아야만 알 수 있다.
#
# 저장하는 것은 제목·링크·시각뿐이다. 본문을 담지 않는 이유는 저작권도
# 있지만, 여기 필요한 신호가 "무엇이 반복되는가"이지 기사 내용이 아니기
# 때문이다.

def _row(item: Item) -> dict:
    return {
        "feed": item.feed, "name": item.name, "layer": item.layer,
        "title": item.title, "url": item.url,
        "published": item.published.isoformat() if item.published else None,
    }


def _item(row: dict) -> Item:
    published = row.get("published")
    return Item(
        feed=row["feed"], name=row.get("name", row["feed"]),
        layer=row.get("layer", "?"), title=row["title"], url=row.get("url", ""),
        published=datetime.fromisoformat(published) if published else None,
    )


def history(days: int = 30, path: Path | None = None) -> list[Item]:
    src = path or STORE
    if not src.exists():
        return []
    out = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = _item(json.loads(line))
        except (json.JSONDecodeError, KeyError):
            continue
        if item.age_days <= days:
            out.append(item)
    return out


def record(items: list[Item], path: Path | None = None) -> int:
    """새로 본 것만 덧붙인다. URL 이 같으면 같은 항목으로 본다."""
    dst = path or STORE
    known = {i.url for i in history(3650, path=dst) if i.url}
    new = [i for i in items if i.url and i.url not in known]
    if new:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("a", encoding="utf-8") as fh:
            for item in new:
                fh.write(json.dumps(_row(item), ensure_ascii=False) + "\n")
    return len(new)


@dataclass
class Persistence:
    word: str
    feeds: int      # 몇 개 채널이 썼나 — 서로 다른 시각인지
    days: int       # 며칠에 걸쳐 나왔나 — 살아남는 이야기인지
    oldest: float   # 가장 오래된 등장이 며칠 전인지


def persistence(items: list[Item], *, min_feeds: int = 2, min_days: int = 1,
                top: int = 20) -> list[Persistence]:
    """같은 말이 며칠에 걸쳐 살아남는지 본다.

    시간창 소속으로 재면 안 된다. 오늘 올라온 항목은 2일 창에도 30일 창에도
    똑같이 들어가므로 모든 단어가 같은 점수를 받는다. 지속성은 **서로 다른
    날짜에 몇 번 나왔는지**로만 드러난다.

    이틀 떠들썩하고 사라지면 헤드라인이고 3주 내내 나오면 구조적 변화다.
    구분하지 않으면 FOMC·CPI 같은 이벤트성 주제만 반복해서 다루게 된다.
    """
    seen_feeds: dict[str, set[str]] = defaultdict(set)
    seen_days: dict[str, set[str]] = defaultdict(set)
    oldest: dict[str, float] = {}

    for item in items:
        if not item.published:
            continue
        day = item.published.astimezone(timezone.utc).strftime("%Y-%m-%d")
        for token in set(TOKEN.findall(item.title)):
            word = token.lower() if token.isascii() else token
            if word in STOPWORDS:
                continue
            seen_feeds[word].add(item.feed)
            seen_days[word].add(day)
            oldest[word] = max(oldest.get(word, 0.0), item.age_days)

    rows = [
        Persistence(w, len(seen_feeds[w]), len(seen_days[w]), oldest[w])
        for w in seen_feeds
        if len(seen_feeds[w]) >= min_feeds and len(seen_days[w]) >= min_days
    ]
    rows.sort(key=lambda p: (-p.days, -p.feeds, -p.oldest, p.word))
    return rows[:top]


# 조사가 붙은 형태와 방송 상투어를 걸러내기 위한 최소 목록. 형태소 분석기를
# 붙이지 않는 이유는, 진짜 신호가 단어 빈도가 아니라 **여러 채널이 같은 주에
# 같은 말을 쓰는지**이기 때문이다. 교차 출현 조건이 잡음을 대부분 없앤다.
STOPWORDS = {
    # 시간·지시
    "이유", "지금", "오늘", "내일", "어제", "올해", "내년", "이번", "다시", "최근",
    # 방송·기사 상투어
    "전체보기", "라이브", "실시간", "방송", "마감시황", "시황", "특집", "인터뷰",
    "속보", "단독", "종합", "영상", "출연", "대표", "교수", "위원", "기자",
    "full", "live", "shorts", "news",
    # 금융 기사에 늘 붙어 다녀 주제를 구분하지 못하는 말
    "투자", "시장", "기업", "경제", "산업", "종목", "주식", "증시", "지수",
    "상승", "하락", "강세", "약세", "전망", "발표", "지원", "확대", "추진",
    "억원", "조원", "만원", "달러", "정부", "글로벌", "국내", "해외", "분기",
    "그리고", "하지만", "때문", "위해", "대해", "관련", "가능", "시작", "종료",
    # 의미를 나르지 않는 일반 명사·관형어
    "있는", "없는", "많은", "모든", "함께", "방식", "사업", "출시", "공개",
    "전환", "집중", "논란", "예고", "진짜", "이것", "저것",
}
TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")

# 뉴스 RSS 는 하루 수십 건을 쏟아내고 유튜브는 주당 대여섯 건이다. 제한을
# 두지 않으면 통신사 기사의 상투어가 채널 신호를 덮는다.
THEME_CAP = 25


def themes(items: list[Item], *, min_feeds: int = 2, top: int = 12,
           cap: int = THEME_CAP) -> list[tuple[str, int, int]]:
    """여러 채널에 걸쳐 반복되는 말을 찾는다.

    한 채널이 같은 말을 열 번 하는 것은 그 채널의 관심사지만, 서로 다른
    채널이 같은 주에 같은 말을 하면 그것이 이번 주 통념이다. 그래서 등장
    횟수가 아니라 **몇 개 피드에 걸쳐 나왔는지**로 정렬한다.

    Returns (단어, 등장 피드 수, 총 등장 횟수).
    """
    per_feed: dict[str, set[str]] = defaultdict(set)
    counts: Counter[str] = Counter()
    taken: Counter[str] = Counter()

    for item in items:  # collect() 가 최신순으로 정렬해 둔다
        if taken[item.feed] >= cap:
            continue
        taken[item.feed] += 1
        for token in set(TOKEN.findall(item.title)):
            word = token.lower() if token.isascii() else token
            if word in STOPWORDS:
                continue
            per_feed[word].add(item.feed)
            counts[word] += 1

    scored = [(w, len(seen), counts[w]) for w, seen in per_feed.items()
              if len(seen) >= min_feeds]
    scored.sort(key=lambda x: (-x[1], -x[2], x[0]))
    return scored[:top]
