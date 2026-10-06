import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from radar.cli import run_brief
from radar.config import load_settings
from radar.html import render_history, render_report
from radar.store import connect, get_brief, list_briefs, save_brief

WEB_FILES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


def serve(root: Path, port: int, host: str = "127.0.0.1") -> None:
    settings = load_settings(root)
    web = root / "web"
    database_path = root / "data" / "radar.sqlite"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._file(web / "index.html")
                return
            if path == "/history.html":
                reports = list_briefs(connect(database_path))
                if reports:
                    self._bytes(render_history(reports, "/").encode(), "text/html; charset=utf-8")
                    return
                self._file(web / "history.html")
                return
            if path.startswith("/r/"):
                report = get_brief(connect(database_path), path.removeprefix("/r/"))
                if report is None:
                    self._bytes("없는 리포트입니다.".encode(), "text/plain; charset=utf-8", 404)
                    return
                self._bytes(render_report(report, "/").encode(), "text/html; charset=utf-8")
                return
            relative = path.lstrip("/")
            candidate = (web / relative).resolve()
            if web.resolve() not in candidate.parents and candidate != web.resolve():
                self._bytes(b"not found", "text/plain", 404)
                return
            if candidate.is_file():
                self._file(candidate)
                return
            self._bytes(b"not found", "text/plain", 404)

        def do_POST(self):
            if urlparse(self.path).path != "/brief":
                self._bytes(b"not found", "text/plain", 404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode() or "{}")
                keyword = str(payload.get("keyword") or "").strip()
                days = int(payload.get("days") or 14)
                if not keyword:
                    raise ValueError("키워드가 비어 있습니다.")
                report = run_brief(
                    keyword,
                    days=days,
                    max_videos=6,
                    max_articles=6,
                    settings=settings,
                )
                save_brief(connect(database_path), report)
            except Exception as exc:
                message = str(exc) or "실패"
                body = json.dumps({"error": message}, ensure_ascii=False).encode()
                self._bytes(body, "application/json; charset=utf-8", 400)
                return
            body = json.dumps({"url": f"/r/{report['id']}"}, ensure_ascii=False).encode()
            self._bytes(body, "application/json; charset=utf-8")

        def log_message(self, fmt, *args):
            print("[radar]", fmt % args)

        def _file(self, path: Path):
            content_type = WEB_FILES.get(path.suffix, "application/octet-stream")
            self._bytes(path.read_bytes(), content_type)

        def _bytes(self, data: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}")
    server.serve_forever()
