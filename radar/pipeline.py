import hashlib
import re
from datetime import datetime, timedelta, timezone

from radar.textutil import (
    STANCE_LABEL,
    TOPICS,
    TOPIC_WORDS,
    STANCE_WORDS,
    compact,
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
    angles = ("칼럼", "재정", "물가", "연준", "상승", "하락")
    aliases = {
        "미국 국채 금리": ["US treasury yield", "treasury supply"],
        "원달러": ["USD KRW"],
    }
    queries = [keyword]
    for angle in angles:
        queries.append(f"{keyword} {angle}".strip())
    queries.extend(aliases.get(keyword, []))
    if keyword.isascii():
        queries.append(f"{keyword} outlook")
    unique = []
    for query in queries:
        cleaned = query.strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return unique[:8]


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


def select_diverse(items: list[dict], *, limit: int, id_key: str, group_key: str, group_cap: int = 1, preserve_order: bool = False) -> list[dict]:
    ranked = list(items) if preserve_order else sorted(items, key=lambda item: item.get("published_at") or "", reverse=True)
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


def _point(source: dict) -> str:
    claim = next((item for item in source.get("claims") or [] if item.get("opinion") or item.get("quote")), None)
    text = ""
    if claim:
        text = (claim.get("opinion") or claim.get("quote") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 80:
        text = text[:79].rstrip() + "…"
    return text


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
        if len(matched) < 2:
            continue
        ups = [source for source in matched if source["stance"] == "up"]
        downs = [source for source in matched if source["stance"] == "down"]
        if ups and downs:
            status = "conflict"
            sides = [
                {"label": "상승 압력", "source_ids": [source["id"] for source in ups], "quote_ok": True},
                {"label": "하락 압력", "source_ids": [source["id"] for source in downs], "quote_ok": True},
            ]
        else:
            seen = set()
            sides = []
            for source in matched:
                point = _point(source)
                key = compact(point)
                if not key or key in seen:
                    continue
                seen.add(key)
                sides.append({"label": point, "source_ids": [source["id"]], "quote_ok": True})
            if len(sides) >= 2:
                status = "distinct"
            else:
                status = "consensus"
                side_label = STANCE_LABEL.get(matched[0]["stance"], "확인된 인용")
                sides = [{"label": side_label, "source_ids": [source["id"] for source in matched], "quote_ok": True}]
        issues.append({
            "id": topic_id,
            "label": label,
            "proposition": proposition,
            "sides": sides,
            "status": status,
        })
    if not any(issue["status"] in {"conflict", "distinct"} for issue in issues):
        overview = _points_issue(sources)
        if overview:
            issues.append(overview)
    return issues


def _points_issue(sources: list[dict]) -> dict | None:
    sides = []
    seen = set()
    for source in sources:
        point = _point(source)
        key = compact(point)
        if not key or key in seen:
            continue
        seen.add(key)
        sides.append({"label": point, "source_ids": [source["id"]], "quote_ok": True})
    if len(sides) < 2:
        return None
    return {
        "id": "points",
        "label": "포인트",
        "proposition": "각 출처가 확인된 인용에서 말하는 것",
        "sides": sides[:4],
        "status": "distinct",
    }


def _delta_label(source: dict, other: dict) -> str:
    source_video = str(source.get("id") or "").startswith("yt_")
    other_video = str(other.get("id") or "").startswith("yt_")
    if source_video and other_video:
        return "다른 영상과 다른 점"
    if not source_video and not other_video:
        return "다른 글과 다른 점"
    return "다른 출처와 다른 점"


def _view_text(source: dict) -> str:
    text = (source.get("summary") or "").strip() or _point(source)
    text = re.sub(r"\s+", " ", text)
    if len(text) > 140:
        text = text[:139].rstrip() + "…"
    return text


def _same_view(source: dict, other: dict) -> bool:
    if {source.get("stance"), other.get("stance")} == {"up", "down"}:
        return False
    left = compact(_point(source))
    right = compact(_point(other))
    if not left or not right:
        return False
    return left == right or left in right or right in left


def apply_deltas(sources: list[dict]) -> None:
    for source in sources:
        video = str(source.get("id") or "").startswith("yt_")
        others = [item for item in sources if item.get("id") != source.get("id")]
        pool = [item for item in others if str(item.get("id") or "").startswith("yt_") == video] or others
        mine = _view_text(source)
        if not pool or not mine:
            source["delta"] = ""
            source["delta_label"] = ""
            continue
        kind = "이 영상은" if video else "이 글은"
        same = [item for item in pool if _same_view(source, item)]
        if len(same) == len(pool):
            titles = "」, 「".join((item.get("title") or "다른 출처") for item in pool[:2])
            source["delta_label"] = "다른 영상과 같은 점" if video else "다른 글과 같은 점"
            source["delta"] = f"「{titles}」과 같은 말을 한다. {kind} {mine}"
            continue
        other = _contrast_with(source, pool) or pool[0]
        source["delta_label"] = _delta_label(source, other)
        source["delta"] = f"{kind} {mine} 「{other.get('title') or '다른 출처'}」은 {_view_text(other)}"


def _contrast_with(source: dict, sources: list[dict]) -> dict | None:
    others = [item for item in sources if item.get("id") != source.get("id") and not _same_view(source, item)]
    if not others:
        return None
    mine = set(source.get("issues") or [])
    my_point = compact(_point(source))

    def rank(item: dict) -> tuple:
        opposite = {source.get("stance"), item.get("stance")} == {"up", "down"}
        same_point = compact(_point(item)) == my_point
        same_topic = bool(mine & set(item.get("issues") or []))
        return (not opposite, same_point, not same_topic)

    others.sort(key=rank)
    return others[0]


def one_line(issues: list[dict], videos: list[dict], articles: list[dict]) -> str:
    if not videos and not articles:
        return "인용으로 확인할 문장이 없다."
    if any(issue["status"] == "conflict" for issue in issues):
        return "확인된 인용 안에서 방향이 갈린 쟁점이 있다."
    if any(issue["status"] == "distinct" for issue in issues):
        return "출처마다 짚는 문장이 다르다."
    return "확인된 인용은 같은 쪽으로 모인다."


def _prefer_watchable(videos: list[dict], keyword: str = "") -> list[dict]:
    kept = []
    for video in videos:
        duration = video.get("duration_sec") or 0
        if 0 < duration < 45:
            continue
        kept.append(video)
    tokens = [part for part in (keyword or "").split() if len(part) >= 2]

    def rank(video: dict) -> int:
        title = video.get("title") or ""
        return sum(token in title for token in tokens)

    kept.sort(key=rank, reverse=True)
    short = [video for video in kept if 0 < (video.get("duration_sec") or 0) <= 360]
    long = [video for video in kept if video not in short]
    merged = []
    while short or long:
        if short:
            merged.append(short.pop(0))
        if long:
            merged.append(long.pop(0))
    return merged


def _finish_source(raw: dict, claims: list[dict], source_id: str, *, trust: bool = False) -> dict | None:
    grounded = list(claims) if trust else keep_grounded(claims, raw.get("text") or "")
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
    video_fn=None,
    compare_fn=None,
    extractor=None,
    skipped: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    extractor = extractor or heuristic_claims
    caption_fn = caption_fn or (lambda _video_id: None)
    skipped = list(skipped or [])
    articles = [item for item in (articles or []) if within_window(item.get("published_at"), days, now)]
    videos = _prefer_watchable(
        [item for item in (videos or []) if within_window(item.get("published_at"), days, now)],
        keyword,
    )
    article_pool = select_diverse(articles, limit=max_articles, id_key="canonical_url", group_key="publisher", group_cap=2)
    video_pool = select_diverse(
        videos,
        limit=max(max_videos * 3, 12),
        id_key="video_id",
        group_key="channel",
        group_cap=1,
        preserve_order=True,
    )

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
    heard_count = 0
    for video in video_pool:
        if len(ready_videos) >= max_videos:
            break
        segments = caption_fn(video.get("video_id") or "")
        video_id = video.get("video_id") or ""
        finished = None
        caption_status = "ok"
        scope = ""
        if segments:
            text = "\n".join(segment.get("text") or "" for segment in segments)
            raw = dict(video)
            raw["text"] = text
            finished = _finish_source(raw, extractor(text, segments), f"yt_{video_id}")
        else:
            heard = video_fn(video) if video_fn else None
            if heard:
                scope = next((claim.get("heard_scope") for claim in heard if claim.get("heard_scope")), "")
                heard_summary = next((claim.get("video_summary") for claim in heard if claim.get("video_summary")), "")
                finished = _finish_source(video, heard, f"yt_{video_id}", trust=True)
                if finished is not None and heard_summary:
                    finished["summary"] = heard_summary
                caption_status = "video"
            else:
                if getattr(video_fn, "stopped", False):
                    break
                captions_missing += 1
        if finished is None:
            if caption_status == "video":
                captions_missing += 1
            continue
        if caption_status == "video":
            heard_count += 1
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
            "caption_status": caption_status,
            "heard_scope": scope,
            "selected_because": "모델이 공개 영상을 봄" if caption_status == "video" else "최근 업로드, 채널당 1개",
        })
        ready_videos.append(finished)

    if heard_count:
        skipped.append(f"youtube_video: 자막 대신 모델이 영상 {heard_count}개를 보고 정리함")
    if captions_missing:
        if video_fn:
            skipped.append(f"youtube_video: 영상 {captions_missing}개는 제한 시간 안에 의견을 받지 못해 건너뜀")
        elif getattr(caption_fn, "reason", "") == "blocked":
            skipped.append(f"youtube_captions: 유튜브가 이 네트워크의 자막 요청을 막아 {captions_missing}개를 건너뜀")
        else:
            skipped.append(f"youtube_captions: 자막을 가져오지 못함 {captions_missing}개")
    note = getattr(video_fn, "note", "") if video_fn else ""
    if note:
        skipped.append(note)
    sources = ready_videos + ready_articles
    apply_deltas(sources)
    if compare_fn and len(ready_videos) >= 2:
        updates = compare_fn(ready_videos) or {}
        for video in ready_videos:
            item = updates.get(video.get("video_id") or "") or updates.get(video.get("id") or "")
            if not item or not item.get("delta"):
                continue
            video["delta"] = item["delta"]
            if item.get("label"):
                video["delta_label"] = item["label"]
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
