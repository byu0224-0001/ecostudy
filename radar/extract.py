import json
import os
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


TEXT_MODELS = (
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
)
_TEXT_MODEL_CHOICE = ""


def gemini_claims(text: str, settings: Settings) -> list[dict] | None:
    global _TEXT_MODEL_CHOICE
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
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    if _TEXT_MODEL_CHOICE and _TEXT_MODEL_CHOICE not in _VIDEO_MODELS_SKIP:
        models = [_TEXT_MODEL_CHOICE]
    else:
        models = []
        for model in (settings.gemini_model, *TEXT_MODELS):
            if model and model not in models and model not in _VIDEO_MODELS_SKIP:
                models.append(model)
    payload = None
    for model in models[:4]:
        payload, note = _generate(settings, body, model=model)
        if note == "gemini: 잠시 혼잡":
            payload, note = _generate(settings, body, model=model)
        if note == "gemini: 요청 한도":
            _VIDEO_MODELS_SKIP.add(model)
            payload = None
            continue
        if payload is None:
            continue
        _TEXT_MODEL_CHOICE = model
        break
    if payload is None:
        return None
    parsed = _json_text(payload)
    if not isinstance(parsed, dict):
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
VIDEO_WATCH_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
    VIDEO_FALLBACK_MODEL,
)
_VIDEO_MODELS_SKIP: set[str] = set()
_VIDEO_MODEL_CHOICE = ""


def body_window(duration: int) -> tuple[int, int, str]:
    """Skip the greeting and watch the stretch where the argument is usually made."""
    length = int(duration or 0)
    if 30 <= length <= 210:
        return 0, length, "영상 전체"
    start = 30
    end = min(length, start + 210) if length else start + 180
    if length and end - start < 90:
        return 0, length, "영상 전체"
    return start, end, f"{start // 60}:{start % 60:02d}–{end // 60}:{end % 60:02d}"


def gemini_video_claims(video: dict, settings: Settings, keyword: str = "") -> tuple[list[dict] | None, str]:
    global _VIDEO_MODEL_CHOICE
    video_id = (video.get("video_id") or "").strip()
    if not video_id or not settings.gemini_key:
        return None, ""
    start, end, scope = body_window(int(video.get("duration_sec") or 0))
    prompt = (
        f"이 유튜브 영상의 {scope} 구간만 들었다. 채널 인사와 제목 읽기는 핵심이 아니다. "
        "화자가 근거를 든 뒤 결론으로 말한 금융 의견만 JSON 객체로 적어라. "
        "summary는 그 결론을 두 문장, 120자 안으로 정리한다. 구간 밖에 있는 말은 넣지 마라. "
        "quote는 들은 문장이고, opinion은 그 문장의 포인트다. "
        "확신이 없으면 claims를 비운다. "
        "stance는 up, down, range, structural, unknown 중 하나다. "
        "start_sec는 그 말이 나온 초다.\n"
        '{"summary":"","claims":[{"quote":"","opinion":"","stance":"unknown","start_sec":null}]}\n\n'
        f"키워드: {keyword}\n영상 제목: {video.get('title') or ''}"
    )
    body = {
        "contents": [{"parts": [
            {
                "file_data": {
                    "file_uri": f"https://www.youtube.com/watch?v={video_id}",
                    "mime_type": "video/mp4",
                },
                "video_metadata": {
                    "start_offset": f"{start}s",
                    "end_offset": f"{end}s",
                    "fps": 0.5,
                },
            },
            {"text": prompt},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    on_vercel = bool(os.environ.get("VERCEL"))
    watch_timeout = 18 if on_vercel else 32
    models = []
    if _VIDEO_MODEL_CHOICE and _VIDEO_MODEL_CHOICE not in _VIDEO_MODELS_SKIP:
        models = [_VIDEO_MODEL_CHOICE]
    else:
        order = ("gemini-3.6-flash", settings.gemini_model, *VIDEO_WATCH_MODELS) if on_vercel else (settings.gemini_model, *VIDEO_WATCH_MODELS)
        for model in order:
            if model and model not in models and model not in _VIDEO_MODELS_SKIP:
                models.append(model)
    if not models:
        return None, "gemini: 요청 한도"
    note = "youtube_video: 영상을 열지 못함"
    attempts = 0
    attempt_cap = 2 if on_vercel else 3
    for model in models:
        if attempts >= attempt_cap:
            break
        payload, note = _generate(settings, body, timeout=watch_timeout, model=model)
        attempts += 1
        if note == "gemini: 잠시 혼잡" and not on_vercel:
            payload, note = _generate(settings, body, timeout=watch_timeout, model=model)
            attempts += 1
        if note == "gemini: 요청 한도":
            _VIDEO_MODELS_SKIP.add(model)
            continue
        claims = [
            claim for claim in (_video_claims(payload) or [])
            if _video_quote_ok(claim.get("quote") or "", keyword or video.get("title") or "")
        ]
        if claims:
            _VIDEO_MODEL_CHOICE = model
            for claim in claims:
                claim["heard_scope"] = scope
            return claims, ""
        if payload is not None:
            _VIDEO_MODEL_CHOICE = model
            return None, "youtube_video: 의견 문장을 찾지 못함"
    return None, note or "youtube_video: 영상을 열지 못함"


def gemini_video_deltas(videos: list[dict], settings: Settings) -> dict[str, dict]:
    if len(videos) < 2 or not settings.gemini_key:
        return {}
    lines = []
    for video in videos:
        quote = next((claim.get("quote") for claim in video.get("claims") or [] if claim.get("quote")), "")
        lines.append(
            f"id={video.get('video_id') or video.get('id')}\n"
            f"제목={video.get('title') or ''}\n"
            f"핵심={video.get('summary') or ''}\n"
            f"인용={quote}"
        )
    prompt = (
        "아래는 각 영상에서 들은 핵심과 인용이다. 이 문장에 없는 사실은 쓰지 마라. "
        "각 id에 대해 relation은 same 또는 different다. "
        "same이면 어떤 말이 같은지 한 문장으로 적고, 차이를 만들지 마라. "
        "different이면 이 영상이 말한 부분과 비교한 영상이 말한 부분을 둘 다 적어라. "
        "비교 영상 제목을 넣어라.\n"
        '{"items":[{"id":"","relation":"different","delta":""}]}\n\n'
        + "\n\n".join(lines)
    )
    model = _VIDEO_MODEL_CHOICE or VIDEO_WATCH_MODELS[0]
    payload, _note = _generate(settings, {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }, timeout=25, model=model)
    parsed = _json_text(payload)
    items = []
    if isinstance(parsed, dict):
        items = parsed.get("items") or []
    elif isinstance(parsed, list):
        items = parsed
    found = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        text = (item.get("delta") or "").strip()
        if not text:
            continue
        relation = item.get("relation")
        label = "다른 영상과 같은 점" if relation == "same" else "다른 영상과 다른 점"
        found[str(item.get("id") or "")] = {"delta": text, "label": label}
    return found


def _json_text(payload: dict | None):
    if not payload:
        return None
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        raw = next(part["text"] for part in reversed(parts) if part.get("text") and not part.get("thought"))
        return json.loads(raw)
    except Exception:
        return None


def _video_items(parsed) -> tuple[str, list]:
    if isinstance(parsed, dict):
        return (parsed.get("summary") or "").strip(), list(parsed.get("claims") or [])
    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict) and parsed[0].get("claims"):
            return (parsed[0].get("summary") or "").strip(), list(parsed[0].get("claims") or [])
        return "", parsed
    return "", []


def _video_claims(payload: dict | None) -> list[dict] | None:
    summary, items = _video_items(_json_text(payload))
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
        stance = item.get("stance")
        if stance == "support":
            stance = "up"
        elif stance in {"oppose", "against"}:
            stance = "down"
        if stance not in {"up", "down", "range", "structural", "unknown"}:
            stance = stance_of(quote)
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
            "stance": stance,
            "video_summary": summary[:180],
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
        if exc.code == 503:
            return None, "gemini: 잠시 혼잡"
        return None, "gemini: 응답 실패"
    except Exception:
        return None, "gemini: 응답 실패"


def _video_quote_ok(quote: str, keyword: str) -> bool:
    text = (quote or "").strip()
    if len(text) < 18:
        return False
    if "기자" in text[:48] and text[:1] in "([":
        return False
    if _usable_quote(text):
        return True
    needles = [part for part in (keyword or "").split() if len(part) >= 2]
    needles.extend(("국채", "금리", "채권", "연준", "수익률"))
    return any(word in text for word in needles)


def _usable_quote(quote: str) -> bool:
    if len(quote) < 24:
        return False
    if "기자" in quote[:48] and quote[:1] in "([":
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
