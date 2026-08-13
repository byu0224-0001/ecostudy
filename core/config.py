"""Paths and credentials.

Layer 1 rule: this module knows where things live and how to authenticate.
It does not know which data series matter. That belongs in a session spec.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is convenience, not a requirement
    def load_dotenv(*_args, **_kwargs):
        return False

ROOT = Path(__file__).resolve().parent.parent

# Load .env from the repo root explicitly. The dividend project loads it from
# the process cwd, which silently breaks depending on where you run from.
load_dotenv(ROOT / ".env")

CACHE_DIR = Path(os.getenv("STUDY_CACHE_DIR") or ROOT / "cache")
SERIES_DIR = Path(os.getenv("STUDY_SERIES_DIR") or ROOT / "series")
SESSIONS_DIR = ROOT / "sessions"


class MissingCredential(RuntimeError):
    """Raised with the env var name so the fix is obvious."""

    def __init__(self, var: str, what: str):
        super().__init__(
            f"{var} 가 설정되지 않았습니다 ({what}). "
            f"{ROOT / '.env'} 에 추가하세요. .env.example 참고."
        )
        self.var = var


def require(var: str, what: str) -> str:
    value = os.getenv(var)
    if not value:
        raise MissingCredential(var, what)
    return value


def optional(var: str) -> str | None:
    return os.getenv(var) or None


def has(*vars_: str) -> bool:
    return all(os.getenv(v) for v in vars_)


# Anything matching these substrings is redacted before a request is recorded
# in the cache. The cache is an audit trail, so it must never hold secrets.
SECRET_PARAM_HINTS = ("key", "token", "secret", "pw", "password", "crtfc", "auth")

REDACTED = "<redacted>"


def redact(params: dict) -> dict:
    out = {}
    for k, v in (params or {}).items():
        if any(hint in k.lower() for hint in SECRET_PARAM_HINTS):
            out[k] = REDACTED
        else:
            out[k] = v
    return out


def _secret_values() -> list[str]:
    """Every credential currently in the environment, longest first.

    Longest first so that a key which contains another as a substring is
    replaced whole rather than leaving a fragment behind.
    """
    found = []
    for name, value in os.environ.items():
        if not value or len(value) < 8:
            continue
        if any(hint in name.lower() for hint in SECRET_PARAM_HINTS):
            found.append(value)
    return sorted(set(found), key=len, reverse=True)


def scrub(text: str) -> str:
    """Remove credentials from a string before it is written to disk.

    Query-string redaction is not enough on its own: ECOS puts the API key in
    the URL path. Rather than expecting every adapter to remember that, any
    credential visible in the environment is stripped here.
    """
    if not text:
        return text
    for secret in _secret_values():
        text = text.replace(secret, REDACTED)
    return text
