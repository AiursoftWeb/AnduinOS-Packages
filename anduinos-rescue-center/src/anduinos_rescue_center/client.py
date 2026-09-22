"""Unprivileged client for the fixed Rescue Center helper."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from .live import is_live_environment


HELPER = "/usr/libexec/anduinos-rescue-center-helper"
LIVE_HELPER = "/usr/libexec/anduinos-rescue-center-live-helper"


def _helper_command() -> list[str]:
    helper = LIVE_HELPER if is_live_environment() else HELPER
    return ["pkexec", helper]


def probe(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    result = run(
        [*_helper_command(), "probe"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Storage scan failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The rescue helper returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The rescue helper protocol is incompatible")
    return payload


def inspect_target(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not path.startswith("/dev/") or len(identity) != 64:
        raise ValueError("Invalid rescue target identity")
    result = run(
        [*_helper_command(), "inspect", path, identity],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Target inspection failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The rescue helper returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The rescue helper protocol is incompatible")
    return payload


def reset_password(
    path: str,
    identity: str,
    username: str,
    password: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    if not path.startswith("/dev/") or len(identity) != 64 or not username:
        raise ValueError("Invalid rescue target identity")
    if not password or len(password) > 4096 or "\n" in password or "\0" in password:
        raise ValueError("The new password is empty or unsupported")
    result = run(
        [*_helper_command(), "reset-password", path, identity, username],
        input=password,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Password reset failed")


def list_files(
    path: str,
    identity: str,
    relative: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(
        ["list-files", path, identity, relative], "File listing failed", run=run
    )


def export_file(
    path: str,
    identity: str,
    relative: str,
    destination: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    payload = _json_action(
        ["export", path, identity, relative, destination], "Export failed", run=run,
        timeout=3600,
    )
    exported = payload.get("exported")
    if not isinstance(exported, str) or not exported:
        raise RuntimeError("The rescue helper did not report the exported file")
    return exported


def list_snapshots(
    path: str,
    identity: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    return _json_action(["list-snapshots", path, identity], "Snapshot listing failed", run=run)


def create_snapshot(
    path: str,
    identity: str,
    title: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not title.strip() or len(title) > 120:
        raise ValueError("Invalid snapshot name")
    return _json_action(
        ["create-snapshot", path, identity, title], "Snapshot creation failed", run=run,
        timeout=3600,
    )


def restore_snapshot(
    path: str,
    identity: str,
    deployment_id: str,
    protect_current: bool,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if len(deployment_id) != 36:
        raise ValueError("Invalid recovery point ID")
    return _json_action(
        ["restore-snapshot", path, identity, deployment_id, str(protect_current).lower()],
        "System restore failed",
        run=run,
        timeout=3600,
    )


def _json_action(
    arguments: list[str],
    failure: str,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
    timeout: int = 180,
) -> dict[str, Any]:
    if len(arguments) < 3 or not arguments[1].startswith("/dev/") or len(arguments[2]) != 64:
        raise ValueError("Invalid rescue target identity")
    result = run(
        [*_helper_command(), *arguments], capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or failure)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The rescue helper returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The rescue helper protocol is incompatible")
    return payload
