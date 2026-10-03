"""Gettext initialisation for AnduinOS Help.

All user-visible strings should pass through :func:`_` so the existing
locale machinery (``compile-locales.sh`` + ``po/`` files) can translate
them. Translators add new languages by copying ``po/anduinos-help.pot``
to ``po/<locale>.po`` and translating the strings.
"""
from __future__ import annotations

import gettext
import os
from pathlib import Path

_DOMAIN = "anduinos-help"
_LOCALE_DIR_ENV = "ANDUINOS_HELP_LOCALE_DIR"


def _resolve_locale_dir() -> Path | None:
    """Return the directory containing compiled ``.mo`` catalogs."""
    env = os.environ.get(_LOCALE_DIR_ENV)
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    # Source-tree layout: po/ → compiled into locale/ by compile-locales.sh
    here = Path(__file__).resolve()
    # src/anduinos_help/i18n.py → up 3 → package root
    src_tree_locale = here.parents[3] / "locale"
    if src_tree_locale.is_dir():
        return src_tree_locale
    # System install
    system = Path("/usr/share/locale")
    if system.is_dir():
        return system
    return None


def _make_translation() -> gettext.NullTranslations:
    locale_dir = _resolve_locale_dir()
    if locale_dir is None:
        return gettext.NullTranslations()
    try:
        return gettext.translation(_DOMAIN, localedir=str(locale_dir), fallback=True)
    except Exception:  # noqa: BLE001
        return gettext.NullTranslations()


_translation = _make_translation()


def gettext(message: str) -> str:
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _translation.ngettext(singular, plural, n)


# Convenient single-letter alias, mirroring the gettext convention.
def _(message: str) -> str:  # noqa: D401
    """Translate ``message`` using the active locale."""
    return gettext(message)
