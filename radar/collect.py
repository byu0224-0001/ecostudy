import json
import shutil
import subprocess
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


def naver_news(query: str, settings: Settings, fetch=fetch_bytes) -> list[dict]:
    params = urllib.parse.urlencode({"query": query, "display": 10, "sort": "date"})
    url = "https://openapi.naver.com/v1/search/news.json?" + params
    payload = json.loads(fetch(url, headers={
        "X-Naver-Client-Id": settings.naver_id,
        "X-Naver-Client-Secret": settings.naver_secret,
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
    else:
        try:
            articles.extend(naver_news(keyword, settings))
        except Exception:
            skipped.append("naver: 실패")
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
