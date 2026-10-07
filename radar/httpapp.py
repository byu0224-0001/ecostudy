import json
import os
import threading
from pathlib import Path

from radar.cli import run_brief
from radar.config import load_settings, on_hosted
from radar.html import render_history, render_report
from radar.store import connect, default_path, get_brief, list_briefs, save_brief

ROOT = Path(__file__).resolve().parent.parent

WEB_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


def card_limits() -> tuple[int, int]:
    if on_hosted():
        articles = 2 if os.environ.get("OPENAI_API_KEY", "").strip() else 1
        return 1, articles
    return 6, 6


def dispatch(method: str, path: str, body: bytes, root: Path | None = None) -> tuple[int, str, bytes]:
    root = root or ROOT
    path = (path or "/").split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    web = root / "web"
    if method == "HEAD":
        method = "GET"
    if method == "POST" and path == "/brief":
        return _brief(body, root)
    if method != "GET":
        return 404, "text/plain; charset=utf-8", b"not found"
    if path == "/health":
        return _json({
            "ok": True,
            "hosted": on_hosted(),
            "openai": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            "naver": bool(os.environ.get("NAVER_CLIENT_ID", "").strip()),
            "gemini": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        }, 200)
    if path in ("/", "/index.html"):
        return _file(web / "index.html")
    if path == "/history.html":
        reports = list_briefs(connect(default_path(root)))
        if reports:
            return 200, "text/html; charset=utf-8", render_history(reports, "/").encode()
        return _file(web / "history.html")
    if path.startswith("/r/"):
        report_id = path.removeprefix("/r/").strip("/")
        report = get_brief(connect(default_path(root)), report_id)
        if report is not None:
            return 200, "text/html; charset=utf-8", render_report(report, "/").encode()
        return 200, "text/html; charset=utf-8", _client_report(report_id).encode()
    relative = path.lstrip("/")
    candidate = (web / relative).resolve()
    web_resolved = web.resolve()
    if candidate != web_resolved and web_resolved not in candidate.parents:
        return 404, "text/plain; charset=utf-8", b"not found"
    if candidate.is_file():
        return _file(candidate)
    return 404, "text/plain; charset=utf-8", b"not found"


def _brief(body: bytes, root: Path) -> tuple[int, str, bytes]:
    try:
        payload = json.loads(body.decode() or "{}")
    except json.JSONDecodeError:
        return _json({"error": "요청을 읽지 못했습니다."}, 400)
    if not isinstance(payload, dict):
        return _json({"error": "요청을 읽지 못했습니다."}, 400)
    if not _password_ok(str(payload.get("password") or "")):
        return _json({"error": "암호가 맞지 않습니다."}, 401)
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        return _json({"error": "키워드가 비어 있습니다."}, 400)
    if len(keyword) > 80:
        return _json({"error": "키워드가 너무 깁니다."}, 400)
    try:
        days = int(payload.get("days") or 14)
    except (TypeError, ValueError):
        days = 14
    days = min(90, max(1, days))
    if not on_hosted():
        return _run_brief(keyword, days, root)
    box: dict = {}

    def run() -> None:
        try:
            box["result"] = _run_brief(keyword, days, root)
        except Exception as exc:
            box["error"] = str(exc) or "실패"

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(42)
    if "result" in box:
        return box["result"]
    if "error" in box:
        return _json({"error": box["error"]}, 400)
    return _json({"error": "공개 주소의 시간 안에 리포트를 끝내지 못했습니다."}, 504)


def _run_brief(keyword: str, days: int, root: Path) -> tuple[int, str, bytes]:
    videos, articles = card_limits()
    try:
        report = run_brief(
            keyword,
            days=days,
            max_videos=videos,
            max_articles=articles,
            settings=load_settings(root),
        )
        save_brief(connect(default_path(root)), report)
    except Exception as exc:
        return _json({"error": str(exc) or "실패"}, 400)
    return _json({
        "url": f"/r/{report['id']}",
        "id": report["id"],
        "html": render_report(report, "/"),
    }, 200)


def _password_ok(provided: str) -> bool:
    expected = os.environ.get("RADAR_PASSWORD", "").strip()
    if not expected:
        return True
    return provided.strip() == expected


def _client_report(report_id: str) -> str:
    safe = "".join(ch for ch in report_id if ch.isalnum())
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#08090a">
<title>리포트 — Opinion Radar</title>
</head>
<body style="background:#08090a;color:#f2f3f5;font-family:sans-serif;padding:24px">
<p id="missing">이 브라우저에 저장된 리포트가 없습니다. 검색을 다시 실행하면 열립니다.</p>
<script>
const html = localStorage.getItem("radar-html-{safe}");
if (html) {{
  document.open();
  document.write(html);
  document.close();
}}
</script>
</body>
</html>
"""


def _json(payload: dict, status: int) -> tuple[int, str, bytes]:
    return status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode()


def _file(path: Path) -> tuple[int, str, bytes]:
    if not path.is_file():
        return 404, "text/plain; charset=utf-8", b"not found"
    return 200, WEB_TYPES.get(path.suffix, "application/octet-stream"), path.read_bytes()
