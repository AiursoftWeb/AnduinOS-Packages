"""Username suggestions derived from a user's display name."""

from __future__ import annotations

import re
import unicodedata

from unidecode import unidecode


DEFAULT_USERNAME_MAX_LENGTH = 16
_NON_USERNAME_CHARACTERS = re.compile(r"[^a-z0-9]")
_LEADING_DIGITS = re.compile(r"^[0-9]+")


def suggest_username(
    full_name: str,
    max_length: int = DEFAULT_USERNAME_MAX_LENGTH,
) -> str:
    """Return a deterministic ASCII username suggestion for ``full_name``."""
    if max_length < 1:
        raise ValueError("max_length must be positive")

    normalized = unicodedata.normalize("NFKC", full_name).strip()
    first_component = normalized.split(maxsplit=1)[0] if normalized else ""
    ascii_name = unidecode(first_component).lower()
    candidate = _NON_USERNAME_CHARACTERS.sub("", ascii_name)
    candidate = _LEADING_DIGITS.sub("", candidate)[:max_length]
    return candidate or "user"[:max_length]
