"""Shared policy for installed user account names."""

from __future__ import annotations

import re


USERNAME_RE = re.compile(r"^[a-z][a-z0-9]{0,15}$")
RESERVED_USERNAMES = frozenset({"root", "live", "ubuntu"})


def is_valid_username(username: str) -> bool:
    """Return whether ``username`` is safe for an installed user account."""
    return bool(USERNAME_RE.fullmatch(username)) and username not in RESERVED_USERNAMES
