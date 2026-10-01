import json
import urllib.request

from radar.config import Settings
from radar.pipeline import heuristic_claims
from radar.textutil import stance_of

USER_AGENT = "OpinionRadar/0.1 (personal research)"


def make_extractor(settings: Settings):
    def extract(text: str, segments: list[dict] | None = None) -> list[dict]:
        if settings.gemini_key:
            claims = gemini_claims(text, settings)
            if claims:
                return claims
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
        "stance는 up, down, range, structural, unknown 중 하나다.\n"
        '{"claims":[{"quote":"","fact":"","interpretation":"","opinion":"","forecast":"","implication":"","evidence":[],"assumptions":[],"stance":"unknown"}]}\n\n'
        f"원문:\n{excerpt}"
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_key}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            payload = json.loads(response.read().decode())
        raw = payload["candidates"][0]["content"]["parts"][0]["text"]
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
