"""Source adapters.

Adding a source means adding a module here and one registry entry. Nothing
else in the codebase needs to change, because callers ask for a source by name
and receive the same Table/Series carriers regardless of upstream.
"""

from __future__ import annotations

from .base import Source
from .dart import Dart
from .ecos import Ecos
from .krx import Krx

_SINGLETONS: dict[str, Source] = {}

REGISTRY = {
    "krx": Krx,
    "dart": Dart,
    "ecos": Ecos,
}


def get(name: str) -> Source:
    """Return a shared adapter instance, so rate limits are respected per source."""
    key = name.lower()
    if key not in REGISTRY:
        raise ValueError(f"'{name}' 소스는 없습니다. 사용 가능: {sorted(REGISTRY)}")
    if key not in _SINGLETONS:
        _SINGLETONS[key] = REGISTRY[key]()
    return _SINGLETONS[key]


__all__ = ["get", "REGISTRY", "Source", "Krx", "Dart", "Ecos"]
