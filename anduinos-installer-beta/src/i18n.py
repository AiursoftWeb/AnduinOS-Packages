"""Runtime-selectable GNU gettext support for the installer.

Unlike ordinary desktop applications, the installer changes its UI language
inside the running process.  Translations are therefore cached per selected
installer language instead of being installed as one process-global `_`.
"""

from __future__ import annotations

from functools import lru_cache
import gettext
import os
from pathlib import Path


DOMAIN = "anduinos-installer-beta"


def _locale_directory() -> Path:
    override = os.environ.get("ANDUINOS_INSTALLER_LOCALE_DIR")
    if override:
        return Path(override)
    source_tree = Path(__file__).resolve().parent.parent / "locale"
    if any(source_tree.glob(f"*/LC_MESSAGES/{DOMAIN}.mo")):
        return source_tree
    return Path("/usr/share/locale")


@lru_cache(maxsize=None)
def translation(language: str) -> gettext.NullTranslations:
    """Return a cached catalog for an installer language code."""
    return gettext.translation(
        DOMAIN,
        localedir=str(_locale_directory()),
        languages=[language],
        fallback=True,
    )


def _(message: str, language: str) -> str:
    """Translate *message* using the language selected in the installer."""
    return translation(language).gettext(message)


def N_(message: str) -> str:
    """Mark a deferred message for catalog extraction without translating it."""
    return message


def clear_translation_cache() -> None:
    """Clear cached catalogs after a test changes the locale directory."""
    translation.cache_clear()
