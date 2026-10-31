"""Strict parser for untrusted os-release files on offline systems."""

from __future__ import annotations

import shlex
from pathlib import Path


def read_os_release(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, encoded = line.split("=", 1)
        if (
            not key
            or not key.replace("_", "").isalnum()
            or not key[0].isalpha()
        ):
            continue
        try:
            parsed = shlex.split(encoded, comments=False, posix=True)
        except ValueError:
            continue
        if len(parsed) == 1:
            values[key] = parsed[0]
        elif encoded == "":
            values[key] = ""
    return values
