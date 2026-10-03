"""Translations shared by the Rescue Center interface and helper."""

from __future__ import annotations

import gettext


DOMAIN = "anduinos-rescue-center"
gettext.bindtextdomain(DOMAIN, "/usr/share/locale")
_translator = gettext.translation(DOMAIN, localedir="/usr/share/locale", fallback=True)


def _(message: str) -> str:
    return _translator.gettext(message) if message else ""
