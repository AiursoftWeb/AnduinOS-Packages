"""HTTP cache layer for documentation updates.

Uses ``requests-cache`` with a SQLite backend so the help app can re-check
for documentation updates cheaply. Falls back to plain ``requests`` if
the cache backend fails (e.g. read-only filesystem).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from anduinos_help.utils.logging import get_logger
from anduinos_help.utils.paths import cache_dir

_log = get_logger("docs.cache")

_CACHE_NAME = "anduinos-help-http"
_CACHE_TTL = 6 * 3600  # 6 hours — remote doc site rarely changes faster


class HttpCache:
    """A thin wrapper around requests.Session with optional caching."""

    def __init__(self, ttl: int = _CACHE_TTL) -> None:
        self._ttl = ttl
        self._session = self._build_session(ttl)

    def _build_session(self, ttl: int) -> Any:
        try:
            import requests_cache
            cache_path = cache_dir() / _CACHE_NAME
            sess = requests_cache.CachedSession(
                cache_name=str(cache_path),
                backend="sqlite",
                expire_after=ttl,
                allowable_codes=(200, 301, 302, 404),
                stale_if_error=True,
            )
            _log.debug("HTTP cache initialised at %s", cache_path)
            return sess
        except Exception as exc:  # noqa: BLE001
            _log.warning("Falling back to plain requests: %s", exc)
            import requests
            return requests.Session()

    @property
    def session(self) -> Any:
        return self._session

    def get(self, url: str, timeout: int = 20) -> "HttpResponse":
        try:
            resp = self._session.get(url, timeout=timeout)
            return HttpResponse(
                status=resp.status_code,
                text=resp.text,
                headers=dict(resp.headers),
                from_cache=getattr(resp, "from_cache", False),
                ok=resp.ok,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("HTTP GET %s failed: %s", url, exc)
            return HttpResponse(status=0, text="", headers={}, from_cache=False, ok=False, error=str(exc))

    def clear(self) -> None:
        try:
            if hasattr(self._session, "cache") and hasattr(self._session, "clear"):
                # type: ignore[attr-defined]
                self._session.clear()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to clear HTTP cache: %s", exc)


class HttpResponse:
    __slots__ = ("status", "text", "headers", "from_cache", "ok", "error")

    def __init__(self, status: int, text: str, headers: dict[str, str], from_cache: bool, ok: bool, error: str | None = None) -> None:
        self.status = status
        self.text = text
        self.headers = headers
        self.from_cache = from_cache
        self.ok = ok
        self.error = error

    @property
    def etag(self) -> str | None:
        return self.headers.get("ETag") or self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("Last-Modified") or self.headers.get("last-modified")
