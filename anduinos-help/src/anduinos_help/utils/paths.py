"""Filesystem path helpers for the AnduinOS Help Center.

Install layout (matches ``anduinos-help.aosproj``)
---------------------------------------------------
* Application code:  ``/usr/lib/python3/dist-packages/anduinos_help/``
* Launcher:          ``/usr/bin/anduinos-help``
* Stylesheet:        ``/usr/share/anduinos-help/style.css``
* Desktop entry:    ``/usr/share/applications/com.anduinos.Help.desktop``
* Icon:             ``/usr/share/icons/hicolor/scalable/apps/com.anduinos.Help.svg``

The application is a **native** GTK4 + Libadwaita help browser. It does
not bundle Markdown at build time — instead, a background job clones
the AnduinOS-Docs git repository on first launch and indexes its
Markdown files into a SQLite database under ``XDG_DATA_HOME``. Search,
navigation, and article rendering all read from that local database,
so the app is fully functional offline after the initial sync.

User-writable state lives under XDG directories so the application is
friendly to a system-wide read-only install. An
``ANDUINOS_HELP_DATA_DIR`` environment variable lets developers point
at a source-tree ``assets/`` directory without installing.
"""
from __future__ import annotations

import os
from pathlib import Path

# Fixed install location for the stylesheet (the only shipped data file).
_INSTALLED_DATA_DIR = Path("/usr/share/anduinos-help")

# Allow developers to override the data directory (for running from a
# source tree without installing). The override must point to a
# directory that contains ``style.css`` — typically ``anduinos-help/assets``
# in the source repo.
_DEV_DATA_DIR_ENV = "ANDUINOS_HELP_DATA_DIR"


def _source_tree_assets_dir() -> Path | None:
    """If running from a source tree, return the path to ``assets/``."""
    here = Path(__file__).resolve()
    # src/anduinos_help/utils/paths.py → up 3 → package root
    pkg_root = here.parents[3]
    candidate = pkg_root / "assets"
    if candidate.is_dir():
        return candidate
    return None


def _xdg(env_var: str, default_subdir: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base) / "anduinos-help"
    return Path.home() / default_subdir / "anduinos-help"


def cache_dir() -> Path:
    """Directory for ephemeral cache data."""
    p = _xdg("XDG_CACHE_HOME", ".cache")
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    """Directory for user data.

    This is where the SQLite database and the cloned docs repository
    live — both are *user data* in the sense that they survive across
    app restarts and are owned by the user, not the system.
    """
    p = _xdg("XDG_DATA_HOME", ".local/share")
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir() -> Path:
    """Directory for runtime state (logs, last-sync metadata)."""
    p = _xdg("XDG_STATE_HOME", ".local/state")
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_root() -> Path:
    """Directory of shipped data files (currently just ``style.css``).

    Resolution order:
      1. ``ANDUINOS_HELP_DATA_DIR`` env var (explicit override)
      2. ``assets/`` directory next to ``src/`` when running from a
         source tree
      3. ``/usr/share/anduinos-help`` (system install)
    """
    env = os.environ.get(_DEV_DATA_DIR_ENV)
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    src_tree = _source_tree_assets_dir()
    if src_tree is not None:
        return src_tree
    return _INSTALLED_DATA_DIR


def style_css_path() -> Path:
    """Path to the application stylesheet."""
    return data_root() / "style.css"


# ----------------------------------------------------------------------
# Docs-sync paths (clone of AnduinOS-Docs + SQLite index)
# ----------------------------------------------------------------------
def docs_repo_dir() -> Path:
    """Directory where the AnduinOS-Docs git repository is cloned.

    Lives under ``XDG_DATA_HOME/anduinos-help/docs-repo/`` so it
    persists across app restarts.
    """
    p = data_dir() / "docs-repo"
    p.mkdir(parents=True, exist_ok=True)
    return p


def docs_repo_root() -> Path:
    """Path to the cloned repository root (contains ``properdocs.yml``)."""
    return docs_repo_dir() / "repo"


def docs_markdown_dir() -> Path:
    """Directory containing the Markdown source files.

    The AnduinOS-Docs repo uses ``Docs/`` as the docs directory (per
    ``properdocs.yml``). We hard-code that here — it's the same value
    DocsViewer uses.
    """
    return docs_repo_root() / "Docs"


def database_path() -> Path:
    """Path to the SQLite database that indexes the docs repository."""
    return data_dir() / "docs.db"


def sync_lock_path() -> Path:
    """Path to a lock file used to prevent concurrent sync runs."""
    return state_dir() / "sync.lock"


def last_sync_path() -> Path:
    """Path to a small JSON file recording the last successful sync."""
    return state_dir() / "last-sync.json"


def ensure_user_dirs() -> None:
    """Create the XDG user directories used by the app."""
    for fn in (cache_dir, data_dir, state_dir):
        fn()
