import json
import urllib.error
import urllib.request

from radar.config import Settings
from radar.pipeline import heuristic_claims
from radar.textutil import STANCE_WORDS, TOPIC_WORDS, compact, keep_grounded, stance_of

USER_AGENT = "OpinionRadar/0.1 (personal research)"


def make_extractor(settings: Settings):
    def extract(text: str, segments: list[dict] | None = None) -> list[dict]:
        if settings.gemini_key and len(text or "") >= 180:
            grounded = [claim for claim in keep_grounded(gemini_claims(text, settings) or [], text) if _usable_quote(claim.get("quote") or "")]
            if grounded:
                for claim in grounded:
                    if claim.get("start_sec") is None:
                        claim["start_sec"] = _start_from(claim.get("quote") or "", segments)
                return grounded
        return heuristic_claims(text, segments)

    extract.__name__ = "gemini" if settings.gemini_key else "heuristic_claims"
    return extract


def gemini_claims(text: str, settings: Settings) -> list[dict] | None:
    excerpt = (text or "")[:8000]
    if not excerpt.strip():
        return None
    prompt = (
        "아래 원문에서 금융 의견만 JSON으로 뽑아라. "
        "quote는 원문에 있는 문장을 한 글자도 바꾸지 말고 복사한다. "
        "원문에 없는 문장은 만들지 말고 claims를 비운다. "
        "stance는 up, down, range, structural, unknown 중 하나다. "
        "opinion은 quote의 포인트를 40자 안으로 줄인 말이다. quote에 없는 사실은 넣지 않는다.\n"
        '{"claims":[{"quote":"","fact":"","interpretation":"","opinion":"","forecast":"","implication":"","evidence":[],"assumptions":[],"stance":"unknown"}]}\n\n'
        f"원문:\n{excerpt}"
    )
    payload, _note = _generate(settings, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    })
    if payload is None:
        return None
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        raw = next(part["text"] for part in reversed(parts) if part.get("text"))
        parsed = json.loads(raw)
    except Exception:
        return None
    claims = []
    for item in (parsed.get("claims") or [])[:3]:
        quote = (item.get("quote") or "").strip()
        if not quote:
            continue
        claims.append({
            "quote": quote,
            "fact": item.get("fact") or "",
            "interpretation": item.get("interpretation") or "",
            "opinion": item.get("opinion") or quote,
            "forecast": item.get("forecast") or "",
            "implication": item.get("implication") or "",
            "evidence": item.get("evidence") or [],
            "assumptions": item.get("assumptions") or [],
            "quote_status": "missing",
            "start_sec": None,
            "stance": item.get("stance") if item.get("stance") in {"up", "down", "range", "structural", "unknown"} else stance_of(quote),
        })
    return claims or None


def grounding_links(payload: dict) -> list[dict]:
    candidate = (payload.get("candidates") or [{}])[0]
    meta = candidate.get("groundingMetadata") or {}
    links = []
    seen = set()
    for chunk in meta.get("groundingChunks") or []:
        web = chunk.get("web") or {}
        uri = (web.get("uri") or "").strip()
        if not uri.startswith("https://") or uri in seen:
            continue
        seen.add(uri)
        links.append({"title": (web.get("title") or "").strip(), "url": uri})
    return links


def gemini_search_links(keyword: str, settings: Settings) -> tuple[list[dict], str]:
    payload, note = _generate(settings, {
        "contents": [{
            "parts": [{
                "text": (
                    f"'{keyword}' 최근 기사에서 서로 다른 의견, 상승 압력과 하락 압력을 찾아 "
                    "기사의 문장을 인용하라."
                ),
            }],
        }],
        "tools": [{"google_search": {}}],
    }, timeout=45)
    if payload is None:
        return [], note or "gemini_search: 기사 링크 없음"
    links = grounding_links(payload)
    if not links:
        return [], "gemini_search: 기사 링크 없음"
    return links, ""


VIDEO_FALLBACK_MODEL = "gemini-3.5-flash"
_VIDEO_MODELS_SKIP: set[str] = set()


def gemini_video_claims(video: dict, settings: Settings, keyword: str = "") -> tuple[list[dict] | None, str]:
    video_id = (video.get("video_id") or "").strip()
    if not video_id or not settings.gemini_key:
        return None, ""
    duration = video.get("duration_sec") or 0
    end = int(duration) if 30 <= duration <= 90 else 90
    scope = "영상 전체" if duration and duration <= end else f"처음 {end}초"
    prompt = (
        f"이 유튜브 영상의 {scope}에서 화자가 실제로 말한 금융 의견만 JSON으로 뽑아라. "
        "quote는 들은 문장에 가깝게 적고, 영상에 없는 전망은 만들지 마라. 확신이 없으면 claims를 비운다. "
        "opinion은 quote의 포인트를 40자 안으로 줄인다. "
        "stance는 up, down, range, structural, unknown 중 하나다. "
        "start_sec는 그 말이 나온 초이고, 모르면 null이다.\n"
        '{"claims":[{"quote":"","opinion":"","stance":"unknown","start_sec":null}]}\n\n'
        f"영상 제목: {video.get('title') or ''}"
    )
    body = {
        "contents": [{"parts": [
            {
                "file_data": {"file_uri": f"https://www.youtube.com/watch?v={video_id}"},
                "video_metadata": {"start_offset": "0s", "end_offset": f"{end}s"},
            },
            {"text": prompt},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    models = []
    for model in (settings.gemini_model, VIDEO_FALLBACK_MODEL):
        if model and model not in models and model not in _VIDEO_MODELS_SKIP:
            models.append(model)
    if not models:
        return None, "gemini: 요청 한도"
    note = "youtube_video: 영상을 열지 못함"
    for model in models:
        payload, note = _generate(settings, body, timeout=22, model=model)
        if note == "gemini: 요청 한도":
            _VIDEO_MODELS_SKIP.add(model)
            continue
        claims = [
            claim for claim in (_video_claims(payload) or [])
            if _video_quote_ok(claim.get("quote") or "", keyword or video.get("title") or "")
        ]
        if claims:
            for claim in claims:
                claim["heard_scope"] = scope
            return claims, ""
        if payload is not None:
            note = "youtube_video: 의견 문장을 찾지 못함"
    return None, note or "youtube_video: 영상을 열지 못함"


def _video_claims(payload: dict | None) -> list[dict] | None:
    if not payload:
        return None
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        raw = next(part["text"] for part in reversed(parts) if part.get("text"))
        parsed = json.loads(raw)
    except Exception:
        return None
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = parsed.get("claims") or []
    else:
        return None
    claims = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        quote = (item.get("quote") or "").strip()
        if not quote:
            continue
        start = item.get("start_sec")
        try:
            start = int(float(start)) if start is not None else None
        except (TypeError, ValueError):
            start = None
        claims.append({
            "quote": quote,
            "fact": "",
            "interpretation": "",
            "opinion": item.get("opinion") or quote,
            "forecast": "",
            "implication": "",
            "evidence": [],
            "assumptions": [],
            "quote_status": "video",
            "start_sec": start,
            "stance": item.get("stance") if item.get("stance") in {"up", "down", "range", "structural", "unknown"} else stance_of(quote),
        })
    return claims or None


def _generate(settings: Settings, body: dict, timeout: int = 40, model: str = "") -> tuple[dict | None, str]:
    if not settings.gemini_key:
        return None, ""
    payload = dict(body)
    config = dict(payload.get("generationConfig") or {})
    config.setdefault("thinkingConfig", {"thinkingLevel": "low"})
    payload["generationConfig"] = config
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model or settings.gemini_model}:generateContent"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "x-goog-api-key": settings.gemini_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode()), ""
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return None, "gemini: 요청 한도"
        return None, "gemini: 응답 실패"
    except Exception:
        return None, "gemini: 응답 실패"


def _video_quote_ok(quote: str, keyword: str) -> bool:
    text = (quote or "").strip()
    if len(text) < 18:
        return False
    if text.startswith("(") and "기자" in text[:48]:
        return False
    if _usable_quote(text):
        return True
    needles = [part for part in (keyword or "").split() if len(part) >= 2]
    needles.extend(("국채", "금리", "채권", "연준", "수익률"))
    return any(word in text for word in needles)


def _usable_quote(quote: str) -> bool:
    if len(quote) < 24:
        return False
    if quote.startswith("(") and "기자" in quote[:48]:
        return False
    return any(word.lower() in quote.lower() for word in TOPIC_WORDS + STANCE_WORDS)


def _start_from(quote: str, segments: list[dict] | None) -> int | None:
    if not segments or not quote:
        return None
    folded = compact(quote)
    for segment in segments:
        text = segment.get("text") or ""
        if quote in text or (folded and folded in compact(text)):
            start = segment.get("start")
            if start is None:
                return None
            return int(start)
    return None
