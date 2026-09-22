"""Bridge to the shared Rust offline snapshot and recovery engine."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from .storage import inspect_mounted_filesystem, mounted_writable, resolve_target


Run = Callable[..., subprocess.CompletedProcess[str]]
ENGINE = "/usr/libexec/anduinos-btrfs-snapshots-manager-offline"


def list_snapshots(
    path: str,
    identity: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> dict[str, object]:
    return _with_target(path, identity, "list", (), run=run, mount_base=mount_base)


def create_snapshot(
    path: str,
    identity: str,
    title: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> dict[str, object]:
    title = title.strip()
    if not title or len(title) > 120 or any(ord(character) < 32 or ord(character) == 127 for character in title):
        raise ValueError("The snapshot name is invalid")
    return _with_target(
        path,
        identity,
        "create",
        (title, "Created from AnduinOS Rescue Center"),
        run=run,
        mount_base=mount_base,
    )


def restore_snapshot(
    path: str,
    identity: str,
    deployment_id: str,
    protect_current: bool,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> dict[str, object]:
    if len(deployment_id) != 36:
        raise ValueError("The recovery point ID is invalid")
    partition = _resolve_offline_btrfs(path, identity, run=run)
    with mounted_writable(
        partition.path, partition.filesystem, run=run, mount_base=mount_base
    ) as top:
        _validate_mounted_target(top, partition.filesystem)
        _engine(top, "resume", (), run=run)
        protection = _engine(top, "protect", (), run=run) if protect_current else None
        restored = _engine(top, "restore", (deployment_id,), run=run)
        restored["protection"] = protection
        return restored


def _with_target(
    path: str,
    identity: str,
    action: str,
    arguments: tuple[str, ...],
    *,
    run: Run,
    mount_base: Path,
) -> dict[str, object]:
    partition = _resolve_offline_btrfs(path, identity, run=run)
    with mounted_writable(
        partition.path, partition.filesystem, run=run, mount_base=mount_base
    ) as top:
        _validate_mounted_target(top, partition.filesystem)
        _engine(top, "resume", (), run=run)
        return _engine(top, action, arguments, run=run)


def _resolve_offline_btrfs(path: str, identity: str, *, run: Run):
    partition = resolve_target(path, identity, run=run)
    if partition.active_system:
        raise RuntimeError("The currently running system cannot be a recovery target")
    if partition.mountpoints:
        raise RuntimeError("Unmount the selected partition before managing snapshots")
    if partition.filesystem != "btrfs":
        raise RuntimeError("System snapshots require the standard AnduinOS Btrfs layout")
    return partition


def _validate_mounted_target(top: Path, filesystem: str) -> None:
    identity = inspect_mounted_filesystem(top, filesystem)
    if identity.os_kind != "anduinos" or not identity.btrfs_layout:
        raise RuntimeError("The selected partition is not a standard AnduinOS Btrfs installation")


def _engine(
    top: Path,
    action: str,
    arguments: tuple[str, ...],
    *,
    run: Run,
) -> dict[str, object]:
    result = run(
        [ENGINE, action, str(top), *arguments],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANGUAGE": "C"},
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Snapshot operation failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("The snapshot engine returned invalid data") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("The snapshot engine protocol is incompatible")
    return payload
