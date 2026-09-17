"""URL & link helpers.

External links are opened with the user's default system browser via
``Gtk.show_uri`` (or a fallback to ``xdg-open``). Internal documentation
links (``anduinos-help://article/<id>``) are dispatched by the window.
"""
from __future__ import annotations

import os
import re
import subprocess
from urllib.parse import urlparse

EXTERNAL_DOMAINS = {
    "docs.anduinos.com",
    "anduinos.com",
    "github.com",
    "raw.githubusercontent.com",
    "api.github.com",
    "gitlab.com",
    "gitlab.freedesktop.org",
    "codeberg.org",
    "wiki.gnome.org",
    "developer.gnome.org",
    "help.gnome.org",
    "www.debian.org",
    "wiki.debian.org",
    "ubuntu.com",
    "wiki.ubuntu.com",
    "help.ubuntu.com",
    "manpages.ubuntu.com",
    "www.kernel.org",
    "freedesktop.org",
    "flatpak.org",
    "snapcraft.io",
    "nixos.org",
    "appimage.org",
}


def is_internal_link(url: str) -> bool:
    """Return True for in-app article links (anduinos-help: scheme)."""
    return url.startswith("anduinos-help://")


def is_external_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https", "mailto"}


def is_safe_external(url: str) -> bool:
    """Validate that an external URL targets an allow-listed domain.

    This is not a security boundary — the user can override in their own
    browser — but it lets the UI show a clear "open in browser" affordance
    only for trustworthy URLs and avoids accidentally opening arbitrary
    schemes like javascript:.
    """
    if not is_external_url(url):
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in EXTERNAL_DOMAINS)


def open_external(url: str) -> bool:
    """Open an external URL in the system browser.

    Uses ``Gio.AppInfo.launch_default_for_uri`` (the recommended GTK4 way)
    and falls back to ``xdg-open`` if GTK is unavailable. Returns True on
    success.
    """
    if not is_safe_external(url):
        return False
    # Preferred GTK4 path — works headless of display pointer issues.
    try:
        from gi.repository import Gio
        # launch_default_for_uri_async needs a callback; use the sync version
        # via launch_default_for_uri which returns a gboolean.
        ok = Gio.AppInfo.launch_default_for_uri(url, None, None)
        if ok:
            return True
    except Exception:  # noqa: BLE001
        pass
    # Fall back to xdg-open
    return _fallback_open(url)


def _fallback_open(url: str) -> bool:
    try:
        if os.environ.get("FLATPAK_ID"):
            subprocess.run(["flatpak-spawn", "--host", "xdg-open", url], check=False)
        else:
            subprocess.run(["xdg-open", url], check=False)
        return True
    except (OSError, FileNotFoundError):
        return False


_INTERNAL_ARTICLE_RE = re.compile(r"^anduinos-help://article/(.+)$")


def parse_internal_article(url: str) -> str | None:
    """Return the article id for an internal link, or None."""
    m = _INTERNAL_ARTICLE_RE.match(url)
    return m.group(1) if m else None
