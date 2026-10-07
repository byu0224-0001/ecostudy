import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from radar.config import Settings, on_hosted
from radar.textutil import strip_html

USER_AGENT = "OpinionRadar/0.1 (personal research)"


def fetch_bytes(url: str, headers: dict | None = None, timeout: int | None = None) -> bytes:
    if timeout is None:
        timeout = 6 if on_hosted() else 20
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
    kept = []
    for _hits, item in ranked:
        title = item.get("title") or ""
        if any(_similar_title(title, chosen.get("title") or "") for chosen in kept):
            continue
        kept.append(item)
        if len(kept) >= limit:
            break
    return kept


def _similar_title(left: str, right: str) -> bool:
    from radar.textutil import compact

    a = compact(left)
    b = compact(right)
    if len(a) < 8 or len(b) < 8:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if short in long:
        return True
    return a[:18] == b[:18]


def mix_topic_links(groups: list[list[dict]], keyword: str, days: int, now: datetime, limit: int = 8) -> list[dict]:
    picked_groups = [select_naver_links(group, keyword, days, now, limit=3) for group in groups]
    merged: list[dict] = []
    seen = set()

    def add(item: dict) -> None:
        url = (item.get("canonical_url") or item.get("url") or "").strip()
        title = item.get("title") or ""
        if not url or url in seen:
            return
        if any(_similar_title(title, chosen.get("title") or "") for chosen in merged):
            return
        seen.add(url)
        merged.append(item)

    for slot in range(3):
        for group in picked_groups:
            if len(merged) >= limit:
                return merged
            if slot < len(group):
                add(group[slot])
    return merged


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


_SKIP_HOSTS = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "google.com",
    "news.google.com",
)


def parse_google_cse(payload: dict) -> list[dict]:
    articles = []
    for item in payload.get("items") or []:
        link = (item.get("link") or "").strip()
        host = urllib.parse.urlparse(link).netloc.removeprefix("www.")
        if not link.startswith("http") or any(host == name or host.endswith("." + name) for name in _SKIP_HOSTS):
            continue
        title = strip_html(item.get("title") or "")
        snippet = _without_title(strip_html(item.get("snippet") or ""), title)
        if not title:
            continue
        articles.append({
            "title": title,
            "publisher": host or "Google",
            "canonical_url": link,
            "published_at": "",
            "section": "column" if any(word in title for word in ("칼럼", "오피니언", "사설", "기고")) else "news",
            "text": f"{title}. {snippet}".strip() if snippet else title,
        })
    return articles


def parse_openai_citations(payload: dict) -> list[dict]:
    articles = []
    seen = set()

    def add(url: str, title: str) -> None:
        link = (url or "").split("?", 1)[0].strip()
        if not link.startswith("https://") or link in seen:
            return
        host = urllib.parse.urlparse(link).netloc.removeprefix("www.")
        if any(host == name or host.endswith("." + name) for name in _SKIP_HOSTS):
            return
        seen.add(link)
        cleaned = strip_html(title or "") or host
        articles.append({
            "title": cleaned,
            "publisher": host or "OpenAI",
            "canonical_url": link,
            "published_at": "",
            "section": "column" if any(word in cleaned for word in ("칼럼", "오피니언", "사설", "기고", "column")) else "news",
            "text": cleaned,
        })

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "url_citation":
                inner = node.get("url_citation") if isinstance(node.get("url_citation"), dict) else node
                add(inner.get("url") or "", inner.get("title") or "")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return articles


def openai_search_links(keyword: str, days: int, settings: Settings) -> tuple[list[dict], str]:
    if not settings.openai_ready:
        return [], "openai: 키 없음"
    body = {
        "model": settings.openai_model or "gpt-6.1-sol",
        "reasoning": {"effort": "low"},
        "tools": [{
            "type": "web_search",
            "external_web_access": True,
            "search_context_size": "low",
            "user_location": {"type": "approximate", "country": "KR", "timezone": "Asia/Seoul"},
        }],
        "max_output_tokens": 500,
        "input": (
            f"키워드 '{keyword}', 최근 {int(days)}일. "
            "한국 포털 시세 기사 말고 칼럼, 리포트, 영문 해설 중에서 "
            "재정·물가·연준·수급처럼 원인이 다른 글의 본문 주소만 찾아라. "
            "시세 마감 숫자만 있는 글은 빼라."
        ),
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {settings.openai_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    wait = 16 if on_hosted() else 22

    def _post() -> dict:
        with urllib.request.urlopen(request, timeout=wait) as response:
            return json.loads(response.read().decode())

    if on_hosted():
        box: dict = {}

        def run() -> None:
            try:
                box["payload"] = _post()
            except urllib.error.HTTPError as exc:
                box["code"] = exc.code
            except Exception:
                box["code"] = 0

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(wait)
        if "payload" not in box:
            if box.get("code") == 429:
                return [], "openai: 요청 한도"
            return [], "openai: 응답 실패"
        payload = box["payload"]
    else:
        try:
            payload = _post()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return [], "openai: 요청 한도"
            return [], "openai: 응답 실패"
        except Exception:
            return [], "openai: 응답 실패"
    links = parse_openai_citations(payload)
    if not links:
        return [], "openai: 기사 링크 없음"
    return links[:6], ""


def google_cse(query: str, days: int, settings: Settings, fetch=fetch_bytes) -> list[dict]:
    korean = any("\uac00" <= char <= "\ud7a3" for char in query)
    params = {
        "key": settings.google_key,
        "cx": settings.google_cx,
        "q": query,
        "num": 5,
        "dateRestrict": f"d{max(1, int(days))}",
        "safe": "active",
        "lr": "lang_ko" if korean else "lang_en",
        "gl": "kr" if korean else "us",
    }
    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(params)
    return parse_google_cse(json.loads(fetch(url)))


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


def ytdlp_prefix(which=shutil.which, home: Path | None = None) -> list[str]:
    binary = which("yt-dlp")
    if not binary:
        local = (home or Path.home()) / ".local" / "bin" / "yt-dlp"
        if local.is_file():
            binary = str(local)
    if binary:
        return [binary]
    return [sys.executable, "-m", "yt_dlp"]


def youtube_ytdlp_search(query: str, limit: int = 8) -> list[dict]:
    completed = subprocess.run(
        ytdlp_prefix() + [
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-end",
            str(limit),
            "--no-warnings",
            f"ytsearch{limit}:{query}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=12 if on_hosted() else 40,
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
    return re.split(r"\s+(?:[|\-–—]|::|<)\s*", title)[0].strip()


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
    timeout = 8 if on_hosted() else 15
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        raw = response.read(400_000)
    return final_url, raw.decode("utf-8", "replace")


def collect_articles(keyword: str, days: int, settings: Settings, queries: list[str]) -> tuple[list[dict], list[str]]:
    articles: list[dict] = []
    skipped: list[str] = []
    short = on_hosted()
    for query in ([] if short else queries):
        try:
            articles.extend(google_news(query, days))
        except Exception:
            skipped.append("google_news: 실패")
            break
    frames = []
    for query in [keyword, *queries]:
        if query not in frames and any("\uac00" <= char <= "\ud7a3" for char in query):
            frames.append(query)
    if short:
        column = f"{keyword} 칼럼".strip()
        frames = [keyword] if column == keyword else [keyword, column]
    else:
        frames = frames[:7]
    groups: list[list[dict]] = []
    now = datetime.now(timezone.utc)
    if not settings.naver_ready:
        skipped.append("naver: 키 없음")
    else:
        try:
            for query in frames:
                groups.append(naver_news(query, settings, sort="sim"))
            if len(mix_topic_links(groups, keyword, days, now)) < 4:
                groups.append(naver_news(keyword, settings, sort="date"))
        except Exception:
            skipped.append("naver: 실패")
    if settings.google_ready:
        english = [query for query in queries if query.isascii()]
        google_queries = english[:2] + frames[:2]
        try:
            for query in google_queries:
                groups.append(google_cse(query, days, settings))
        except Exception:
            skipped.append("google_search: 실패")
    else:
        skipped.append("google_search: 키 없음")
    if settings.openai_ready:
        try:
            found, note = openai_search_links(keyword, days, settings)
            if found:
                groups.append(found)
            if note:
                skipped.append(note)
        except Exception:
            skipped.append("openai: 실패")
    else:
        skipped.append("openai: 키 없음")
    links = [
        {
            "url": item.get("canonical_url") or "",
            "title": item.get("title") or "",
            "published_at": item.get("published_at") or "",
            "section": item.get("section") or "news",
        }
        for item in mix_topic_links(groups, keyword, days, now)
    ]
    pages, page_skips = articles_from_pages(links, fetch_article_html, limit=2 if short else 6)
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
    keyword = queries[0] if queries else ""
    frames = [keyword]
    if not on_hosted():
        for suffix in ("전망", "해설", "상승"):
            framed = f"{keyword} {suffix}".strip()
            if framed not in frames:
                frames.append(framed)
    search_limit = 4 if on_hosted() else 8
    seen = set()
    for query in frames:
        try:
            found = youtube_ytdlp_search(query, limit=search_limit)
        except FileNotFoundError:
            skipped.append("youtube: yt-dlp 없음")
            break
        except Exception:
            continue
        for video in found:
            video_id = video.get("video_id") or ""
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            videos.append(video)
    if not videos:
        skipped.append("youtube: 검색 실패")
    return videos, skipped
