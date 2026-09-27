"""Bounded mounts for an explicitly selected offline AnduinOS system."""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .model import Partition
from .storage import inspect_mounted_filesystem, mounted_writable, resolve_target


Run = Callable[..., subprocess.CompletedProcess[str]]


def system_root(top: Path, partition: Partition) -> Path:
    """Resolve the installed root without following a subvolume symlink out."""
    root = top / "@root" if partition.filesystem == "btrfs" else top
    if not root.is_dir() or not root.resolve(strict=True).is_relative_to(top.resolve(strict=True)):
        raise RuntimeError("The selected system root is unavailable or unsafe")
    if inspect_mounted_filesystem(top, partition.filesystem).os_kind != "anduinos":
        raise RuntimeError("The selected partition is not an AnduinOS installation")
    return root


def _mount_directory(root: Path, relative: str) -> Path:
    current = root
    for part in relative.split("/"):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o755)
            metadata = current.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f"Unsafe target mount directory: /{relative}")
    return current


def _checked_mount(run: Run, command: list[str]) -> None:
    result = run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or f"Mount failed: {command[0]}")


def _provide_dns(
    runtime: Path,
    sources: tuple[Path, ...] = (Path("/run/systemd/resolve/resolv.conf"), Path("/etc/resolv.conf")),
) -> None:
    """Make Live DNS available only inside the temporary chroot /run."""
    for source in sources:
        try:
            if not source.is_file() or source.stat().st_size > 64 * 1024:
                continue
            content = source.read_text(encoding="utf-8")
            nameservers = [line.split()[1] for line in content.splitlines()
                           if line.startswith("nameserver ") and len(line.split()) >= 2]
            if not nameservers or all(address.startswith("127.") or address == "::1"
                                      for address in nameservers):
                continue
            resolved = runtime / "systemd/resolve"
            resolved.mkdir(mode=0o755, parents=True, exist_ok=True)
            for name in ("resolv.conf", "stub-resolv.conf"):
                (resolved / name).write_text(content, encoding="utf-8")
            return
        except (OSError, UnicodeError):
            continue


@contextmanager
def opened_system(
    path: str,
    identity: str,
    *,
    esp: str = "",
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> Iterator[tuple[Path, Partition]]:
    """Open one offline root for chroot work; never reuse a stale UI device path."""
    partition = resolve_target(path, identity, run=run)
    if partition.active_system or partition.mountpoints:
        raise RuntimeError("Unmount the offline installation before opening it for repair")
    with mounted_writable(partition.path, partition.filesystem, run=run, mount_base=mount_base) as top:
        root = system_root(top, partition)
        mounts: list[tuple[Path, bool]] = []
        try:
            if esp:
                destination = _mount_directory(root, "boot/efi")
                _checked_mount(run, ["mount", "-t", "vfat", "-o", "rw", "--", esp, str(destination)])
                mounts.append((destination, False))
            for source in ("dev", "sys"):
                destination = _mount_directory(root, source)
                _checked_mount(run, ["mount", "--rbind", f"/{source}", str(destination)])
                mounts.append((destination, True))
                _checked_mount(run, ["mount", "--make-rslave", str(destination)])
            proc = _mount_directory(root, "proc")
            _checked_mount(run, ["mount", "-t", "proc", "proc", str(proc)])
            mounts.append((proc, False))
            runtime = _mount_directory(root, "run")
            _checked_mount(run, ["mount", "-t", "tmpfs", "-o", "mode=0755,nosuid,nodev", "tmpfs", str(runtime)])
            mounts.append((runtime, False))
            _provide_dns(runtime)
            yield root, partition
        finally:
            failures = []
            for destination, recursive in reversed(mounts):
                command = ["umount", "-R", str(destination)] if recursive else ["umount", "--", str(destination)]
                result = run(command, capture_output=True, text=True, timeout=30, check=False)
                if result.returncode:
                    failures.append(str(destination))
            if failures:
                raise RuntimeError("Could not unmount recovery filesystems: " + ", ".join(failures))


def emergency_shell(path: str, identity: str, *, run: Run = subprocess.run) -> int:
    """Attach an expert shell to the selected system from a Live terminal."""
    from .live import is_trusted_live_environment

    if not is_trusted_live_environment() or not os.isatty(0) or not os.isatty(1):
        raise RuntimeError("An interactive AnduinOS Live terminal is required")
    with opened_system(path, identity, run=run) as (root, partition):
        shell = "/bin/bash" if (root / "bin/bash").is_file() else "/bin/sh"
        print(f"\nOffline AnduinOS: {partition.path} ({partition.filesystem})")
        print("This is a root shell in the selected offline system. Type exit to unmount it.\n")
        result = run(
            ["chroot", str(root), shell], check=False,
            env={"HOME": "/root", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM": os.environ.get("TERM", "xterm-256color")},
        )
        return result.returncode
