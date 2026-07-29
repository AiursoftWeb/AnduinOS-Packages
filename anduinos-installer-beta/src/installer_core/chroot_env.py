"""Isolated and reversible execution environment for target commands."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .steps import FailurePolicy, InstallContext


@dataclass(frozen=True)
class FileBackup:
    kind: str
    data: bytes = b""
    mode: int = 0o644
    link_target: str = ""


@dataclass
class EnterChrootStep:
    runner: CommandRunner
    target: Path = Path("/target")
    id: str = "enter-chroot"
    title: str = "Prepare isolated target environment"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 2
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        self.runner.require_commands(("mount", "umount", "findmnt"))
        for relative in ("dev", "proc", "sys", "run"):
            # Global preflight runs before MountTargetStep.execute(), so the
            # target cannot yet exist in context.values. Inspect the executor's
            # configured mountpoint directly.
            path = self.target / relative
            result = self.runner.run(
                ("findmnt", "--noheadings", "--mountpoint", str(path)),
                check=False,
                timeout=10,
            )
            if result.returncode == 0:
                raise RuntimeError(f"Unexpected existing target mount: {path}")

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        mounts: list[Path] = []
        context.values["chroot_mounts"] = mounts

        for relative in ("dev", "proc", "sys", "run"):
            (target / relative).mkdir(parents=True, exist_ok=True)

        self.runner.run(
            ("mount", "--rbind", "/dev", str(target / "dev")), timeout=30
        )
        mounts.append(target / "dev")
        self.runner.run(
            ("mount", "--make-rslave", str(target / "dev")), timeout=30
        )

        self.runner.run(
            ("mount", "-t", "proc", "proc", str(target / "proc")), timeout=30
        )
        mounts.append(target / "proc")

        self.runner.run(
            ("mount", "--rbind", "/sys", str(target / "sys")), timeout=30
        )
        mounts.append(target / "sys")
        self.runner.run(
            ("mount", "--make-rslave", str(target / "sys")), timeout=30
        )

        # Never expose the live host's systemd and D-Bus sockets to the target.
        self.runner.run(
            (
                "mount",
                "-t",
                "tmpfs",
                "-o",
                "mode=0755,nosuid,nodev",
                "tmpfs",
                str(target / "run"),
            ),
            timeout=30,
        )
        mounts.append(target / "run")

        resolver = target / "etc/resolv.conf"
        policy = target / "usr/sbin/policy-rc.d"
        context.values["chroot_resolver_backup"] = _backup_file(resolver)
        context.values["chroot_policy_backup"] = _backup_file(policy)
        _replace_regular_file(
            resolver,
            Path("/etc/resolv.conf").read_bytes(),
            0o644,
        )
        _replace_regular_file(
            policy,
            b"#!/bin/sh\n# Installed temporarily by AnduinOS Installer.\nexit 101\n",
            0o755,
        )
        context.values["chroot_environment_ready"] = True

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        policy = target / "usr/sbin/policy-rc.d"
        if not policy.is_file() or not os.access(policy, os.X_OK):
            raise RuntimeError("policy-rc.d isolation is not active")
        for path in context.values.get("chroot_mounts", []):
            result = self.runner.run(
                ("findmnt", "--noheadings", "--mountpoint", str(path)),
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Chroot mount verification failed: {path}")

    def cleanup(self, context: InstallContext) -> None:
        _leave_chroot(context, self.runner)


@dataclass
class LeaveChrootStep:
    runner: CommandRunner
    id: str = "leave-chroot"
    title: str = "Close isolated target environment"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        return None

    def execute(self, context: InstallContext) -> None:
        _leave_chroot(context, self.runner, check=True)

    def verify(self, context: InstallContext) -> None:
        if context.values.get("chroot_mounts"):
            raise RuntimeError("Chroot mounts remain after cleanup")
        if context.values.get("chroot_environment_ready"):
            raise RuntimeError("Chroot environment state remains active")

    def cleanup(self, context: InstallContext) -> None:
        _leave_chroot(context, self.runner)


def _leave_chroot(
    context: InstallContext,
    runner: CommandRunner,
    *,
    check: bool = False,
) -> None:
    target = context.values.get("target")
    if not isinstance(target, Path):
        return

    resolver_backup = context.values.pop("chroot_resolver_backup", None)
    policy_backup = context.values.pop("chroot_policy_backup", None)
    if isinstance(resolver_backup, FileBackup):
        _restore_file(target / "etc/resolv.conf", resolver_backup)
    if isinstance(policy_backup, FileBackup):
        _restore_file(target / "usr/sbin/policy-rc.d", policy_backup)

    mounts = context.values.get("chroot_mounts", [])
    failed_paths: set[Path] = set()
    for path in reversed(mounts):
        result = runner.run(
            ("umount", "--recursive", str(path)),
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            failed_paths.add(path)
    remaining = [path for path in mounts if path in failed_paths]
    context.values["chroot_mounts"] = remaining
    context.values["chroot_environment_ready"] = bool(remaining)
    if check and remaining:
        raise RuntimeError(
            "Could not unmount chroot filesystems: "
            + ", ".join(str(path) for path in remaining)
        )


def _backup_file(path: Path) -> FileBackup:
    if path.is_symlink():
        return FileBackup("symlink", link_target=os.readlink(path))
    if not path.exists():
        return FileBackup("absent")
    if not path.is_file():
        raise RuntimeError(f"Refusing to replace non-regular file: {path}")
    file_stat = path.stat()
    return FileBackup(
        "file",
        data=path.read_bytes(),
        mode=stat.S_IMODE(file_stat.st_mode),
    )


def _replace_regular_file(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        path.unlink()
    path.write_bytes(data)
    path.chmod(mode)


def _restore_file(path: Path, backup: FileBackup) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()
    if backup.kind == "absent":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup.kind == "symlink":
        path.symlink_to(backup.link_target)
    elif backup.kind == "file":
        path.write_bytes(backup.data)
        path.chmod(backup.mode)
    else:
        raise RuntimeError(f"Unknown file backup type: {backup.kind}")


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target
