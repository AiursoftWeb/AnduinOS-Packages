"""Narrow offline repair operations owned by the privileged helper."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .storage import (
    _local_users,
    inspect_mounted_filesystem,
    mounted_writable,
    resolve_target,
)


Run = Callable[..., subprocess.CompletedProcess[str]]
USERNAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")


def reset_password(
    path: str,
    identity: str,
    username: str,
    password: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> None:
    partition = resolve_target(path, identity, run=run)
    if partition.active_system:
        raise RuntimeError("The currently running system cannot be a rescue target")
    if partition.mountpoints:
        raise RuntimeError("Unmount the selected partition before changing a password")
    with mounted_writable(
        partition.path, partition.filesystem, run=run, mount_base=mount_base
    ) as top:
        root = top / "@root" if partition.filesystem == "btrfs" and (top / "@root").is_dir() else top
        if inspect_mounted_filesystem(top, partition.filesystem).os_kind != "anduinos":
            raise RuntimeError("The selected partition is not an AnduinOS installation")
        reset_password_in_root(root, username, password, run=run)


def reset_password_in_root(
    root: Path,
    username: str,
    password: str,
    *,
    run: Run = subprocess.run,
) -> None:
    if not USERNAME.fullmatch(username):
        raise ValueError("Invalid local account name")
    if not password or len(password) > 4096 or "\n" in password or "\0" in password:
        raise ValueError("The new password is empty or unsupported")
    accounts = {str(user["name"]) for user in _local_users(root)}
    if username not in accounts:
        raise RuntimeError("The selected local account no longer exists")
    result = run(
        ["/usr/sbin/chpasswd", "--root", str(root)],
        input=f"{username}:{password}\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Password reset failed")
    sync = run(
        ["sync", "-f", str(root / "etc/shadow")], capture_output=True,
        text=True, timeout=30, check=False,
    )
    if sync.returncode != 0:
        raise RuntimeError((sync.stderr or sync.stdout).strip() or "Could not sync the new password")
