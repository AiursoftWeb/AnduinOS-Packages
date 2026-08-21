"""Unprivileged client for the restricted Secure Boot helper."""

from __future__ import annotations

import json
import subprocess
from typing import Any


HELPER = "/usr/libexec/anduinos-secureboot-helper"


def run_action(action: str, timeout: int = 1800) -> tuple[int, dict[str, Any]]:
    if action not in {"prepare", "repair-dkms"}:
        raise ValueError(f"unsupported action: {action}")
    completed = subprocess.run(
        ["pkexec", HELPER, action],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = completed.stdout.strip().splitlines()
    if output:
        try:
            return completed.returncode, json.loads(output[-1])
        except json.JSONDecodeError:
            pass
    return completed.returncode, {
        "schema": 1,
        "error": (completed.stderr or completed.stdout).strip() or "unknown-error",
    }
