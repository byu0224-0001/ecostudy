"""Content-addressed store for raw upstream responses.

This exists to make a session auditable. Every number that ends up in a
briefing can be traced back to the exact bytes an official API returned, and
re-derived later without network access. That is what lets a verifier check an
AI-written claim against the source instead of trusting it.

Secrets are redacted before the request is recorded.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config

EXT_BY_CONTENT_TYPE = {
    "application/json": "json",
    "text/json": "json",
    "application/zip": "zip",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/csv": "csv",
    "text/html": "html",
    "text/plain": "txt",
}


def _ext(content_type: str) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    return EXT_BY_CONTENT_TYPE.get(base, "bin")


def key_for(source: str, dataset: str, params: dict) -> str:
    canonical = json.dumps(
        {"source": source, "dataset": dataset, "params": config.redact(params)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


@dataclass
class Entry:
    path: Path
    meta: dict

    @property
    def body(self) -> bytes:
        return (self.path / self.meta["raw_file"]).read_bytes()

    @property
    def age_seconds(self) -> float:
        return time.time() - self.meta["fetched_at_epoch"]

    @property
    def fetched_at(self) -> str:
        return self.meta["fetched_at"]


def _dir_for(source: str, dataset: str, cache_key: str) -> Path:
    return config.CACHE_DIR / source / dataset / cache_key


def lookup(source: str, dataset: str, params: dict, *, max_age: float | None = None) -> Entry | None:
    """Return a cached entry, or None if absent or older than max_age seconds."""
    path = _dir_for(source, dataset, key_for(source, dataset, params))
    meta_file = path / "meta.json"
    if not meta_file.exists():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not (path / meta.get("raw_file", "")).exists():
        return None
    entry = Entry(path=path, meta=meta)
    if max_age is not None and entry.age_seconds > max_age:
        return None
    return entry


def store(
    source: str,
    dataset: str,
    params: dict,
    *,
    body: bytes,
    content_type: str,
    url: str,
    status: int,
) -> Entry:
    cache_key = key_for(source, dataset, params)
    path = _dir_for(source, dataset, cache_key)
    path.mkdir(parents=True, exist_ok=True)

    raw_file = f"raw.{_ext(content_type)}"
    (path / raw_file).write_bytes(body)

    now = datetime.now(timezone.utc)
    meta = {
        "source": source,
        "dataset": dataset,
        "params": config.redact(params),
        # drop the query string, then scrub the remainder: some APIs put the
        # credential in the path itself
        "url": config.scrub(url.split("?")[0]),
        "status": status,
        "content_type": content_type,
        "raw_file": raw_file,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "fetched_at": now.isoformat(),
        "fetched_at_epoch": now.timestamp(),
        "cache_key": cache_key,
    }
    (path / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return Entry(path=path, meta=meta)
