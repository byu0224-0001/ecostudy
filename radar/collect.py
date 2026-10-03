import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from radar.config import Settings
from radar.textutil import strip_html

USER_AGENT = "OpinionRadar/0.1 (personal research)"


def fetch_bytes(url: str, headers: dict | None = None, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_google_rss(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    articles = []
    for item in root.findall("./channel/item"):
        title = strip_html(item.findtext("title") or "")
        publisher = strip_html(item.findtext("source") or "")
        title, publisher = _split_title(title, publisher)
        link = (item.findtext("link") or "").strip()
        description = _without_title(strip_html(item.findtext("description") or ""), title)
        published = (item.findtext("pubDate") or "").strip()
        text = f"{title}. {description}".strip() if description else title
        if not title or not text:
            continue
        articles.append({
            "title": title.strip(),
            "publisher": publisher.strip() or "Google News",
            "canonical_url": link,
            "published_at": _iso_or_raw(published),
            "section": "column" if any(word in title for word in ("칼럼", "오피니언", "사설")) else "news",
            "text": text,
        })
    return articles


def parse_naver(payload: dict) -> list[dict]:
    articles = []
    for item in payload.get("items") or []:
        title = strip_html(item.get("title") or "")
        title, _tail = _split_title(title, "")
        description = _without_title(strip_html(item.get("description") or ""), title)
        if not title:
            continue
        articles.append({
            "title": title,
            "publisher": "네이버 뉴스",
            "canonical_url": item.get("originallink") or item.get("link") or "",
            "published_at": _iso_or_raw(item.get("pubDate") or ""),
            "section": "column" if any(word in title for word in ("칼럼", "오피니언", "사설")) else "news",
            "text": f"{title}. {description}".strip() if description else title,
        })
    return articles


def parse_ytdlp(payload: dict) -> list[dict]:
    videos = []
    for entry in payload.get("entries") or []:
        video_id = entry.get("id") or ""
        if not video_id:
            continue
        timestamp = entry.get("timestamp") or entry.get("release_timestamp")
        published = ""
        if timestamp:
            published = datetime.fromtimestamp(int(timestamp), timezone.utc).strftime("%Y-%m-%d")
        videos.append({
            "video_id": video_id,
            "title": entry.get("title") or "",
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "published_at": published,
            "duration_sec": entry.get("duration"),
            "url": entry.get("url") or f"https://youtu.be/{video_id}",
            "language": "",
            "subscriber_bucket": "unknown",
        })
    return videos


def parse_youtube_api(payload: dict) -> list[dict]:
    videos = []
    for item in payload.get("items") or []:
        video_id = ((item.get("id") or {}).get("videoId")) or ""
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        published = snippet.get("publishedAt") or ""
        videos.append({
            "video_id": video_id,
            "title": snippet.get("title") or "",
            "channel": snippet.get("channelTitle") or "",
            "published_at": published[:10],
            "duration_sec": None,
            "url": f"https://youtu.be/{video_id}",
            "language": "",
            "subscriber_bucket": "unknown",
        })
    return videos


def _split_title(title: str, publisher: str) -> tuple[str, str]:
    if " - " not in title:
        return title.strip(), publisher.strip()
    head, tail = title.rsplit(" - ", 1)
    tail = tail.strip()
    head = head.strip()
    if not publisher or publisher == tail:
        return head, publisher or tail
    return title.strip(), publisher.strip()


def _without_title(description: str, title: str) -> str:
    text = description.strip()
    if title and text.startswith(title):
        text = text[len(title):].strip(" -")
    return text.strip()


def _iso_or_raw(value: str) -> str:
    from radar.pipeline import parse_when

    parsed = parse_when(value)
    if parsed is None:
        return value
    return parsed.strftime("%Y-%m-%d")


def google_news(query: str, days: int, fetch=fetch_bytes) -> list[dict]:
    korean = any("\uac00" <= char <= "\ud7a3" for char in query)
    params = {
        "q": f"{query} when:{days}d",
        "hl": "ko" if korean else "en-US",
        "gl": "KR" if korean else "US",
        "ceid": "KR:ko" if korean else "US:en",
    }
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    return parse_google_rss(fetch(url))


_TOKEN_FORMS = {
    "미국": ("미국", "美"),
    "국채": ("국채", "treasury", "Treasury"),
    "금리": ("금리", "yield", "Yield"),
    "원달러": ("원달러", "원/달러"),
}

_BOILER_MARKERS = (
    "관련기사",
    "관련 기사",
    "관련뉴스",
    "관련 뉴스",
    "추천뉴스",
    "추천 뉴스",
    "함께 보면",
    "많이 본",
    "실시간 급상승",
    "Taboola",
    "저작권자",
    "재배포 금지",
    "무단전재",
    "무단 전재",
)


def keyword_tokens(keyword: str) -> list[str]:
    return [part for part in re.split(r"\s+", (keyword or "").strip()) if len(part) >= 2]


_ANCHOR_TOKENS = {"국채", "원달러"}


def topic_hit_tokens(text: str, keyword: str) -> set[str]:
    tokens = keyword_tokens(keyword)
    hits = set()
    for token in tokens:
        if any(form in text for form in _TOKEN_FORMS.get(token, (token,))):
            hits.add(token)
    if "미국" in tokens and "국채" in tokens and any(
        form in text for form in ("미국채", "미 국채", "美 국채", "美국채", "US Treasury", "U.S. Treasury")
    ):
        hits.update(("미국", "국채"))
    return hits


def topic_hits(text: str, keyword: str) -> int:
    return len(topic_hit_tokens(text, keyword))


def select_naver_links(items: list[dict], keyword: str, days: int, now: datetime, limit: int = 8) -> list[dict]:
    from radar.pipeline import within_window

    tokens = keyword_tokens(keyword)
    need = 1 if len(tokens) <= 1 else min(2, len(tokens))
    ranked = []
    seen = set()
    for item in items:
        url = (item.get("canonical_url") or item.get("url") or "").strip()
        if not url or url in seen:
            continue
        if not within_window(item.get("published_at"), days, now):
            continue
        title = item.get("title") or ""
        hits = topic_hit_tokens(f"{title}\n{item.get('text') or ''}", keyword)
        if len(hits) < need or any(token in _ANCHOR_TOKENS and token not in hits for token in tokens):
            continue
        seen.add(url)
        ranked.append((len(hits), item))
    ranked.sort(key=lambda pair: -pair[0])
    return [item for _hits, item in ranked[:limit]]


def naver_news(query: str, settings: Settings, fetch=fetch_bytes, *, sort: str = "sim", display: int = 15) -> list[dict]:
    params = urllib.parse.urlencode({
        "query": query,
        "display": display,
        "sort": sort,
        "format": "json",
    })
    url = "https://naverapihub.apigw.ntruss.com/search/v1/news?" + params
    payload = json.loads(fetch(url, headers={
        "X-NCP-APIGW-API-KEY-ID": settings.naver_id,
        "X-NCP-APIGW-API-KEY": settings.naver_secret,
    }))
    return parse_naver(payload)


def youtube_api_search(query: str, days: int, settings: Settings, fetch=fetch_bytes) -> list[dict]:
    published = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = urllib.parse.urlencode({
        "part": "snippet",
        "type": "video",
        "order": "date",
        "maxResults": 8,
        "q": query,
        "publishedAfter": published,
        "key": settings.youtube_key,
    })
    url = "https://www.googleapis.com/youtube/v3/search?" + params
    return parse_youtube_api(json.loads(fetch(url)))


def youtube_ytdlp_search(query: str, limit: int = 8) -> list[dict]:
    binary = shutil.which("yt-dlp")
    if binary is None:
        local = "/home/ubuntu/.local/bin/yt-dlp"
        binary = local if shutil.os.path.exists(local) else None
    if binary is None:
        raise FileNotFoundError("yt-dlp")
    completed = subprocess.run(
        [binary, "--flat-playlist", "--dump-single-json", "--playlist-end", str(limit), "--no-warnings", f"ytsearch{limit}:{query}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=40,
    )
    return parse_ytdlp(json.loads(completed.stdout))


def fetch_caption_segments(video_id: str) -> list[dict] | None:
    from youtube_transcript_api import YouTubeTranscriptApi

    fetched = YouTubeTranscriptApi().fetch(video_id, languages=("ko", "en"))
    segments = []
    for item in fetched:
        text = (getattr(item, "text", "") or "").replace("\n", " ").strip()
        if not text:
            continue
        start = getattr(item, "start", None)
        segments.append({"text": text, "start": start})
    return segments or None


def html_to_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    cleaned = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", cleaned)
    for pattern in (
        r'(?is)<[^>]+itemprop=["\']articleBody["\'][^>]*>(.{200,20000})',
        r'(?is)<(?:div|section|article)[^>]+class=["\'][^"\']*article-body[^"\']*["\'][^>]*>(.{200,20000})',
        r'(?is)<article[^>]*>(.{200,20000})</article>',
    ):
        match = re.search(pattern, cleaned)
        if not match:
            continue
        text = _tidy_text(_cut_boilerplate(_strip_byline(strip_html(match.group(1))[:3500])))
        if len(text) >= 180:
            return text
    paragraphs = [strip_html(part) for part in re.findall(r"(?is)<p[^>]*>(.*?)</p>", cleaned)]
    paragraphs = [part for part in paragraphs if len(part) >= 40]
    if len(" ".join(paragraphs)) >= 180:
        return _tidy_text(_cut_boilerplate(_strip_byline(" ".join(paragraphs)[:3500])))
    return _tidy_text(_cut_boilerplate(_strip_byline(strip_html(cleaned)[:3500])))


def _cut_boilerplate(text: str) -> str:
    cut = len(text)
    for marker in _BOILER_MARKERS:
        index = text.find(marker)
        if index >= 80:
            cut = min(cut, index)
    return text[:cut].strip()


def _strip_byline(text: str) -> str:
    return re.sub(r"\([^)]{0,40}\)\s*[가-힣A-Za-z ]{0,16}기자\s*=\s*", "", text).strip()


def _tidy_text(text: str) -> str:
    cleaned = re.sub(r"(?:/)?사진\s*=\s*\S+", " ", text or "")
    cleaned = re.sub(r"\[[^\]]{0,16}사진[^\]]{0,16}\]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def page_title(html: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    if not match:
        return ""
    title = strip_html(match.group(1))
    return re.split(r"\s+(?:[|\-–—]|::)\s*", title)[0].strip()


def published_in_html(html: str) -> str:
    match = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', html or "")
    return match.group(1) if match else ""


def articles_from_pages(links: list[dict], fetch_html, limit: int = 4) -> tuple[list[dict], list[str]]:
    candidates = []
    for link in links:
        url = (link.get("url") or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            candidates.append(link)
        if len(candidates) >= limit + 4:
            break

    def load(link: dict) -> dict | None:
        url = (link.get("url") or "").strip()
        try:
            final_url, html = fetch_html(url)
            text = html_to_text(html)
        except Exception:
            return None
        if len(text) < 180:
            return None
        title = page_title(html) or (link.get("title") or "").strip() or final_url
        host = urllib.parse.urlparse(final_url).netloc.removeprefix("www.")
        return {
            "title": title,
            "publisher": host or (link.get("title") or "").strip() or "웹",
            "canonical_url": final_url or url,
            "published_at": published_in_html(html) or (link.get("published_at") or ""),
            "section": "column" if any(word in title for word in ("칼럼", "오피니언", "사설")) else (link.get("section") or "news"),
            "text": text,
        }

    loaded: list[dict | None]
    if not candidates:
        loaded = []
    elif len(candidates) == 1:
        loaded = [load(candidates[0])]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            loaded = list(pool.map(load, candidates))
    articles = [item for item in loaded if item][:limit]
    failures = len(loaded) - len([item for item in loaded if item])
    skipped = []
    if failures and not articles:
        skipped.append("article_body: 본문을 열지 못함")
    elif failures:
        skipped.append(f"article_body: 본문 확인 실패 {failures}건")
    return articles, skipped


def fetch_article_html(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        final_url = response.geturl()
        raw = response.read(400_000)
    return final_url, raw.decode("utf-8", "replace")


def collect_articles(keyword: str, days: int, settings: Settings, queries: list[str]) -> tuple[list[dict], list[str]]:
    articles: list[dict] = []
    skipped: list[str] = []
    for query in queries:
        try:
            articles.extend(google_news(query, days))
        except Exception:
            skipped.append("google_news: 실패")
            break
    if not settings.naver_ready:
        skipped.append("naver: 키 없음")
        return articles, skipped
    frames = [keyword]
    for suffix in ("상승", "하락"):
        framed = f"{keyword} {suffix}".strip()
        if framed not in frames:
            frames.append(framed)
    found: list[dict] = []
    try:
        for sort in ("sim", "date"):
            for query in frames:
                found.extend(naver_news(query, settings, sort=sort))
            picked = select_naver_links(found, keyword, days, datetime.now(timezone.utc))
            if len(picked) >= 4:
                break
    except Exception:
        skipped.append("naver: 실패")
        return articles, skipped
    links = [
        {
            "url": item.get("canonical_url") or "",
            "title": item.get("title") or "",
            "published_at": item.get("published_at") or "",
            "section": item.get("section") or "news",
        }
        for item in select_naver_links(found, keyword, days, datetime.now(timezone.utc))
    ]
    pages, page_skips = articles_from_pages(links, fetch_article_html, limit=6)
    articles.extend(pages)
    skipped.extend(page_skips)
    return articles, skipped


def collect_videos(queries: list[str], days: int, settings: Settings) -> tuple[list[dict], list[str]]:
    videos: list[dict] = []
    skipped: list[str] = []
    if settings.youtube_ready:
        for query in queries[:3]:
            try:
                videos.extend(youtube_api_search(query, days, settings))
            except Exception:
                skipped.append("youtube_api: 실패")
                break
        return videos, skipped
    try:
        videos.extend(youtube_ytdlp_search(queries[0], limit=8))
    except Exception:
        skipped.append("youtube: 검색 실패")
    return videos, skipped
