"""Base for source adapters.

An adapter knows how to fetch from one upstream. It does NOT know which series
matter for a given study topic — that is decided per session, because the
answer changes with the market regime. Keeping that knowledge out of here is
the single rule that keeps this layer stable.

Every fetch goes through the cache, so a repeated run is free and a past run
stays auditable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .. import cache
from ..http import Client, FetchError

DAY = 86400.0


@dataclass
class Fetched:
    data: object
    cache_key: str
    fetched_at: str
    from_cache: bool


class Source:
    name: str = ""
    citation: str = ""
    min_interval: float = 0.2
    timeout: float = 20.0

    def __init__(self):
        self.client = Client(
            self.name,
            min_interval=self.min_interval,
            timeout=self.timeout,
            headers=self.default_headers(),
        )

    def default_headers(self) -> dict:
        return {}

    def _fetch(
        self,
        dataset: str,
        url: str,
        *,
        params: dict,
        query: dict | None = None,
        auth: dict | None = None,
        auth_headers: dict | None = None,
        max_age: float = DAY,
        refresh: bool = False,
        method: str = "GET",
    ) -> tuple[bytes, str, str, bool]:
        """Return (body, cache_key, fetched_at, from_cache).

        `params` identifies the request for caching. `query` is what actually
        goes on the wire, defaulting to `params` because for most APIs they
        are the same thing. ECOS is the exception: it encodes everything in
        the URL path, so it passes an empty query while still needing stable
        cache identity.

        `auth` (query) and `auth_headers` (header) carry credentials into the
        request only; neither reaches disk. Header auth is resolved per call
        rather than on the session, so constructing an adapter never requires
        a credential the caller may not need.
        """
        if not refresh:
            hit = cache.lookup(self.name, dataset, params, max_age=max_age)
            if hit:
                return hit.body, hit.meta["cache_key"], hit.fetched_at, True

        wire = params if query is None else query
        request_params = {**wire, **(auth or {})}
        if method == "GET":
            resp = self.client.get(url, params=request_params, headers=auth_headers)
        else:
            resp = self.client.post(url, data=request_params, headers=auth_headers)

        entry = cache.store(
            self.name,
            dataset,
            params,
            body=resp.body,
            content_type=resp.content_type,
            url=resp.url,
            status=resp.status,
        )
        return resp.body, entry.meta["cache_key"], entry.fetched_at, False

    def get_json(self, dataset: str, url: str, **kw) -> Fetched:
        body, key, at, cached = self._fetch(dataset, url, **kw)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError(
                f"{self.name}/{dataset}: JSON 파싱 실패 — 응답 앞부분: {body[:200]!r}"
            ) from exc
        return Fetched(data=data, cache_key=key, fetched_at=at, from_cache=cached)

    def get_bytes(self, dataset: str, url: str, **kw) -> Fetched:
        body, key, at, cached = self._fetch(dataset, url, **kw)
        return Fetched(data=body, cache_key=key, fetched_at=at, from_cache=cached)


def ymd(value) -> str:
    """Normalise a date-ish value to the YYYYMMDD form both KRX and DART want."""
    import pandas as pd

    if value is None:
        raise ValueError("날짜가 필요합니다")
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return text
    return pd.to_datetime(text).strftime("%Y%m%d")
