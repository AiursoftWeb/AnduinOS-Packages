"""Mount-namespace isolation for the privileged installer executor."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence


def isolate_mount_namespace(
    *,
    unshare: Callable[[int], None] = os.unshare,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Keep every installer mount private to the executor process tree."""

    try:
        unshare(os.CLONE_NEWNS)
    except OSError as error:
        raise RuntimeError(
            f"Could not create a private installer mount namespace: {error}"
        ) from error

    command: Sequence[str] = ("/usr/bin/mount", "--make-rprivate", "/")
    try:
        result = run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            f"Could not make the installer mount namespace private: {error}"
        ) from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            "Could not make the installer mount namespace private"
            + (f": {detail}" if detail else "")
        )
