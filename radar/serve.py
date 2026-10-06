from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from radar.httpapp import dispatch


def serve(root: Path, port: int, host: str = "127.0.0.1") -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._send("GET")

        def do_POST(self):
            self._send("POST")

        def do_HEAD(self):
            self._send("HEAD")

        def _send(self, method: str):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            status, content_type, data = dispatch(method, urlparse(self.path).path, raw, root)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(data)

        def log_message(self, fmt, *args):
            print("[radar]", fmt % args)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}")
    server.serve_forever()
