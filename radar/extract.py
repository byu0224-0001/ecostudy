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


def _generate(settings: Settings, body: dict, timeout: int = 40) -> tuple[dict | None, str]:
    if not settings.gemini_key:
        return None, ""
    payload = dict(body)
    config = dict(payload.get("generationConfig") or {})
    config.setdefault("thinkingConfig", {"thinkingLevel": "low"})
    payload["generationConfig"] = config
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
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
