"""Filesystem path helpers for the AnduinOS Help Center.

Install layout (matches ``anduinos-help.aosproj``)
---------------------------------------------------
* Application code:  ``/usr/lib/python3/dist-packages/anduinos_help/``
* Launcher:          ``/usr/bin/anduinos-help``
* Bundled docs:     ``/usr/share/anduinos-help/``  (articles/, index.json, meta.json, style.css)
* Desktop entry:    ``/usr/share/applications/com.anduinos.Help.desktop``
* Icon:             ``/usr/share/icons/hicolor/scalable/apps/com.anduinos.Help.svg``

All user-writable state lives under XDG directories so the application is
friendly to a system-wide read-only install. An ``ANDUINOS_HELP_DATA_DIR``
environment variable lets developers run from a source tree without
installing.
"""
from __future__ import annotations

import os
from pathlib import Path

# Fixed install location for bundled docs/assets.
_INSTALLED_DATA_DIR = Path("/usr/share/anduinos-help")

# Allow developers to override the data directory (for running from a
# source tree without installing). The override must point to a directory
# that contains ``articles/``, ``index.json``, ``meta.json`` and
# ``style.css`` — typically ``anduinos-help/assets`` in the source repo.
_DEV_DATA_DIR_ENV = "ANDUINOS_HELP_DATA_DIR"

# When running from a source tree (no env override), look for the
# ``assets`` directory next to ``src/``. This is used by ``unittest``
# runs in CI per the DEV_GUIDE pattern: ``PYTHONPATH=src python3 -m
# unittest discover -s tests``.
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
    """Directory for HTTP cache and downloaded doc bundles (ephemeral)."""
    p = _xdg("XDG_CACHE_HOME", ".cache")
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    """Directory for user data (custom notes, preferences)."""
    p = _xdg("XDG_DATA_HOME", ".local/share")
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_dir() -> Path:
    """Directory for runtime state (logs, last-sync metadata)."""
    p = _xdg("XDG_STATE_HOME", ".local/state")
    p.mkdir(parents=True, exist_ok=True)
    return p


def bundled_docs_dir() -> Path:
    """Directory of documentation shipped with the application.

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


def articles_dir() -> Path:
    """Directory of bundled article content (markdown + metadata)."""
    return bundled_docs_dir() / "articles"


def index_path() -> Path:
    return bundled_docs_dir() / "index.json"


def meta_path() -> Path:
    return bundled_docs_dir() / "meta.json"


def style_css_path() -> Path:
    return bundled_docs_dir() / "style.css"


def local_cache_articles_dir() -> Path:
    """Directory where downloaded docs are unpacked for atomic swap."""
    return cache_dir() / "docs-staging"


def local_cache_index_path() -> Path:
    return cache_dir() / "docs-index.json"


def asset_path(*parts: str) -> Path:
    return bundled_docs_dir() / Path(*parts)


def ensure_user_dirs() -> None:
    for fn in (cache_dir, data_dir, state_dir):
        fn()
