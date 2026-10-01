import hashlib
import re
from datetime import datetime, timedelta, timezone

from radar.textutil import (
    STANCE_LABEL,
    TOPICS,
    TOPIC_WORDS,
    STANCE_WORDS,
    keep_grounded,
    keywords_in,
    split_sentences,
    stance_of,
)


def parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    iso = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
        if not match:
            return None
        parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def within_window(value: str | None, days: int, now: datetime) -> bool:
    parsed = parse_when(value)
    if parsed is None:
        return True
    return parsed >= now - timedelta(days=days)


def expand_queries(keyword: str) -> list[str]:
    queries = [keyword, f"{keyword} 상승", f"{keyword} 하락"]
    aliases = {
        "미국 국채 금리": ["US treasury yield", "10-year treasury yield"],
        "원달러": ["USD KRW"],
    }
    queries.extend(aliases.get(keyword, []))
    if keyword.isascii():
        queries.append(f"{keyword} outlook")
    unique = []
    for query in queries:
        cleaned = query.strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return unique[:6]


def heuristic_claims(text: str, segments: list[dict] | None = None) -> list[dict]:
    claims = []
    for sentence in split_sentences(text):
        if len(sentence) < 12 or len(sentence) > 180:
            continue
        if not any(word.lower() in sentence.lower() for word in TOPIC_WORDS + STANCE_WORDS):
            continue
        claims.append(_claim(sentence, _start_for(sentence, segments), stance_of(sentence)))
        if len(claims) >= 2:
            break
    return claims


def _claim(sentence: str, start: int | None, stance: str) -> dict:
    return {
        "quote": sentence,
        "fact": sentence if re.search(r"\d", sentence) else "",
        "interpretation": "",
        "opinion": sentence,
        "forecast": "",
        "implication": "",
        "evidence": [],
        "assumptions": [],
        "quote_status": "missing",
        "start_sec": start,
        "stance": stance,
    }


def _start_for(quote: str, segments: list[dict] | None) -> int | None:
    if not segments:
        return None
    from radar.textutil import compact

    folded = compact(quote)
    for segment in segments:
        text = segment.get("text") or ""
        if quote in text or (folded and folded in compact(text)):
            start = segment.get("start")
            if start is None:
                return None
            return int(start)
    return None


def _source_stance(claims: list[dict]) -> str:
    counts: dict[str, int] = {}
    for claim in claims:
        stance = claim.get("stance") or "unknown"
        counts[stance] = counts.get(stance, 0) + 1
    if not counts:
        return "unknown"
    return max(counts, key=lambda key: (counts[key], key != "unknown"))


def _issues_for(blob: str) -> list[str]:
    found = []
    for topic_id, words, _label, _proposition in TOPICS:
        if any(word.lower() in blob.lower() for word in words):
            found.append(topic_id)
    return found


def select_diverse(items: list[dict], *, limit: int, id_key: str, group_key: str, group_cap: int = 1) -> list[dict]:
    ranked = sorted(items, key=lambda item: item.get("published_at") or "", reverse=True)
    seen_ids = set()
    group_counts: dict[str, int] = {}
    kept = []
    for item in ranked:
        identity = item.get(id_key) or item.get("title")
        if identity in seen_ids:
            continue
        group = (item.get(group_key) or "").strip().lower()
        if group and group_counts.get(group, 0) >= group_cap:
            continue
        seen_ids.add(identity)
        if group:
            group_counts[group] = group_counts.get(group, 0) + 1
        kept.append(item)
        if len(kept) >= limit:
            break
    return kept


def build_issues(sources: list[dict]) -> list[dict]:
    issues = []
    ups = [source for source in sources if source["stance"] == "up"]
    downs = [source for source in sources if source["stance"] == "down"]
    if ups and downs:
        issues.append({
            "id": "direction",
            "label": "방향",
            "proposition": "확인된 인용이 상승 압력을 말하는가",
            "sides": [
                {"label": "상승 압력", "source_ids": [source["id"] for source in ups], "quote_ok": True},
                {"label": "하락 압력", "source_ids": [source["id"] for source in downs], "quote_ok": True},
            ],
            "status": "conflict",
        })
    for topic_id, words, label, proposition in TOPICS:
        matched = []
        for source in sources:
            blob = " ".join(claim.get("quote") or "" for claim in source["claims"])
            if any(word.lower() in blob.lower() for word in words):
                matched.append(source)
        if not matched:
            continue
        ups = [source for source in matched if source["stance"] == "up"]
        downs = [source for source in matched if source["stance"] == "down"]
        if ups and downs:
            status = "conflict"
            sides = [
                {"label": "상승 압력", "source_ids": [source["id"] for source in ups], "quote_ok": True},
                {"label": "하락 압력", "source_ids": [source["id"] for source in downs], "quote_ok": True},
            ]
        elif len(matched) >= 2 and len({source["stance"] for source in matched}) == 1:
            status = "consensus"
            side_label = STANCE_LABEL.get(matched[0]["stance"], "확인된 인용")
            sides = [{"label": side_label, "source_ids": [source["id"] for source in matched], "quote_ok": True}]
        else:
            continue
        issues.append({
            "id": topic_id,
            "label": label,
            "proposition": proposition,
            "sides": sides,
            "status": status,
        })
    return issues


def apply_deltas(videos: list[dict]) -> None:
    for video in videos:
        opposite = next(
            (
                other
                for other in videos
                if other["id"] != video["id"]
                and {video["stance"], other["stance"]} == {"up", "down"}
            ),
            None,
        )
        if opposite is None:
            video["delta"] = ""
            continue
        video["delta"] = (
            f"「{opposite['title']}」은 {STANCE_LABEL[opposite['stance']]}으로 인용된다. "
            f"이 영상은 {STANCE_LABEL[video['stance']]}으로 인용된다."
        )


def one_line(issues: list[dict], videos: list[dict], articles: list[dict]) -> str:
    if not videos and not articles:
        return "인용으로 확인할 문장이 없다."
    if any(issue["status"] == "conflict" for issue in issues):
        return "확인된 인용 안에서 방향이 갈린 쟁점이 있다."
    return "확인된 인용은 한쪽으로 모이거나, 같은 명제로 붙지 않는다."


def _finish_source(raw: dict, claims: list[dict], source_id: str) -> dict | None:
    grounded = keep_grounded(claims, raw.get("text") or "")
    if not grounded:
        return None
    blob = " ".join(claim["quote"] for claim in grounded)
    summary = " ".join(claim["opinion"] for claim in grounded if claim.get("opinion"))
    return {
        "id": source_id,
        "claims": [{key: claim[key] for key in (
            "quote", "fact", "interpretation", "opinion", "forecast", "implication",
            "evidence", "assumptions", "quote_status", "start_sec",
        )} for claim in grounded],
        "stance": _source_stance(grounded),
        "keywords": keywords_in(blob),
        "issues": _issues_for(blob),
        "summary": summary,
        "delta": "",
    }


def build_brief(
    keyword: str,
    *,
    days: int = 14,
    max_videos: int = 6,
    max_articles: int = 6,
    articles: list[dict] | None = None,
    videos: list[dict] | None = None,
    caption_fn=None,
    extractor=None,
    skipped: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    extractor = extractor or heuristic_claims
    caption_fn = caption_fn or (lambda _video_id: None)
    skipped = list(skipped or [])
    articles = [item for item in (articles or []) if within_window(item.get("published_at"), days, now)]
    videos = [item for item in (videos or []) if within_window(item.get("published_at"), days, now)]
    article_pool = select_diverse(articles, limit=max_articles, id_key="canonical_url", group_key="publisher", group_cap=2)
    video_pool = select_diverse(videos, limit=max_videos, id_key="video_id", group_key="channel", group_cap=1)

    ready_articles = []
    for index, article in enumerate(article_pool, start=1):
        finished = _finish_source(article, extractor(article.get("text") or "", None), f"art_{index}")
        if finished is None:
            continue
        finished.update({
            "title": article.get("title") or "",
            "publisher": article.get("publisher") or "",
            "canonical_url": article.get("canonical_url") or "",
            "published_at": article.get("published_at") or "",
            "section": article.get("section") or "news",
        })
        ready_articles.append(finished)

    ready_videos = []
    captions_missing = 0
    for video in video_pool:
        segments = caption_fn(video.get("video_id") or "")
        if not segments:
            captions_missing += 1
            continue
        text = "\n".join(segment.get("text") or "" for segment in segments)
        raw = dict(video)
        raw["text"] = text
        finished = _finish_source(raw, extractor(text, segments), f"yt_{video.get('video_id')}")
        if finished is None:
            continue
        video_id = video.get("video_id") or ""
        finished.update({
            "video_id": video_id,
            "url": video.get("url") or (f"https://youtu.be/{video_id}" if video_id else ""),
            "title": video.get("title") or "",
            "channel": video.get("channel") or "",
            "published_at": video.get("published_at") or "",
            "duration_sec": video.get("duration_sec"),
            "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else "",
            "language": video.get("language") or "",
            "subscriber_bucket": video.get("subscriber_bucket") or "unknown",
            "caption_status": "ok",
            "selected_because": "최근 업로드, 채널당 1개",
        })
        ready_videos.append(finished)

    if captions_missing:
        skipped.append(f"youtube_captions: 자막을 가져오지 못함 {captions_missing}개")
    apply_deltas(ready_videos)
    sources = ready_videos + ready_articles
    issues = build_issues(sources)
    generated = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    report_id = hashlib.sha256(f"{keyword}|{generated}".encode()).hexdigest()[:12]
    return {
        "id": report_id,
        "keyword": keyword,
        "window_days": days,
        "generated_at": generated,
        "sample": False,
        "queries": expand_queries(keyword),
        "pool": {
            "youtube_seen": len(videos),
            "youtube_kept": len(ready_videos),
            "articles_seen": len(articles),
            "articles_kept": len(ready_articles),
        },
        "one_line": one_line(issues, ready_videos, ready_articles),
        "issues": issues,
        "videos": ready_videos,
        "articles": ready_articles,
        "blind_spot": None,
        "skipped": skipped,
        "extractor": getattr(extractor, "__name__", "custom"),
    }
