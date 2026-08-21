"""Convert a transient UI password into a crypt-compatible hash."""

from __future__ import annotations

import subprocess
from collections.abc import Callable


class PasswordHashError(RuntimeError):
    pass


def hash_password(
    password: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    if not password or any(character in password for character in "\x00\r\n"):
        raise PasswordHashError("Password is empty or contains invalid characters")
    try:
        result = run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PasswordHashError(f"Could not hash password: {error}") from error
    password_hash = result.stdout.strip()
    if result.returncode != 0 or not password_hash.startswith("$6$"):
        raise PasswordHashError(
            result.stderr.strip() or "OpenSSL did not return a password hash"
        )
    return password_hash

