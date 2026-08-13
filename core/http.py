"""HTTP with per-source rate limiting and retry.

The dividend project calls `requests` directly from every module, so retry and
backoff were reimplemented several times and rate limits were per-call sleeps.
This is the single place that behaviour lives now.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

import requests

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
RETRYABLE_EXC = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)


class FetchError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str
    url: str
    elapsed_ms: int


class Client:
    """One client per upstream source, so rate limits do not interfere.

    min_interval is the floor between requests to the same source. It is
    enforced with a lock, so concurrent callers queue instead of bursting.
    """

    def __init__(
        self,
        name: str,
        *,
        min_interval: float = 0.2,
        timeout: float = 20.0,
        max_attempts: int = 4,
        headers: dict | None = None,
    ):
        self.name = name
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "study-os/0.1 (investment study research tool)",
                "Accept-Encoding": "gzip, deflate",
                **(headers or {}),
            }
        )
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def _throttle(self):
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
        timeout: float | None = None,
    ) -> Response:
        last_error: Exception | None = None

        for attempt in range(self.max_attempts):
            if attempt:
                # exponential backoff with jitter; jitter matters when several
                # series are fetched from the same source in one run
                delay = min(0.8 * (2 ** (attempt - 1)), 8.0)
                time.sleep(delay + random.uniform(0, 0.3))

            self._throttle()
            started = time.monotonic()
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=timeout or self.timeout,
                )
            except RETRYABLE_EXC as exc:
                last_error = exc
                continue

            elapsed_ms = int((time.monotonic() - started) * 1000)

            if resp.status_code in RETRYABLE_STATUS:
                # honour Retry-After when the server tells us how long to wait
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        time.sleep(min(float(retry_after), 30.0))
                    except ValueError:
                        pass
                last_error = FetchError(
                    f"{self.name}: HTTP {resp.status_code}",
                    status=resp.status_code,
                    url=resp.url,
                )
                continue

            if not resp.ok:
                # 4xx other than the retryable ones means the request is wrong,
                # not that the server is busy. Fail immediately and loudly.
                raise FetchError(
                    f"{self.name}: HTTP {resp.status_code} — {resp.text[:200]}",
                    status=resp.status_code,
                    url=resp.url,
                )

            return Response(
                status=resp.status_code,
                body=resp.content,
                content_type=resp.headers.get("Content-Type", ""),
                url=resp.url,
                elapsed_ms=elapsed_ms,
            )

        raise FetchError(
            f"{self.name}: {self.max_attempts}회 시도 후 실패 — {last_error}",
            url=url,
        )

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> Response:
        return self.request("POST", url, **kw)
