"""Bounded mounts for an explicitly selected offline AnduinOS system."""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .i18n import _ as tr
from .model import Partition
from .storage import inspect_mounted_filesystem, mounted_writable, resolve_target


Run = Callable[..., subprocess.CompletedProcess[str]]


def system_root(top: Path, partition: Partition) -> Path:
    """Resolve the installed root without following a subvolume symlink out."""
    root = top / "@root" if partition.filesystem == "btrfs" else top
    if not root.is_dir() or not root.resolve(strict=True).is_relative_to(top.resolve(strict=True)):
        raise RuntimeError(tr("The selected system root is unavailable or unsafe"))
    if inspect_mounted_filesystem(top, partition.filesystem).os_kind != "anduinos":
        raise RuntimeError(tr("The selected partition is not an AnduinOS installation"))
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
            raise RuntimeError(tr("Unsafe target mount directory: /{path}").format(path=relative))
    return current


def _checked_mount(run: Run, command: list[str]) -> None:
    result = run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip() or tr("Mount failed: {command}").format(command=command[0]))


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
    mount_home: bool = False,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> Iterator[tuple[Path, Partition]]:
    """Open one offline root for chroot work; never reuse a stale UI device path."""
    partition = resolve_target(path, identity, run=run)
    if partition.active_system or partition.mountpoints:
        raise RuntimeError(tr("Unmount the offline installation before opening it for repair"))
    with mounted_writable(partition.path, partition.filesystem, run=run, mount_base=mount_base) as top:
        inspected_root = system_root(top, partition)
        root = inspected_root
        root_mount: Path | None = None
        mounts: list[tuple[Path, bool]] = []
        try:
            if partition.filesystem == "btrfs":
                # GRUB probes the device behind /boot/grub from the target's
                # mount table. Chrooting into top/@root as an ordinary directory
                # loses that mount boundary; the installer mounts @root directly.
                root_mount = Path(tempfile.mkdtemp(prefix="system-", dir=mount_base))
                _checked_mount(run, ["mount", "-t", "btrfs", "-o", "rw,subvol=@root",
                                     "--", partition.path, str(root_mount)])
                mounts.append((root_mount, False))
                root = root_mount
                if mount_home and ((top / "@home").exists() or (top / "@home").is_symlink()):
                    _mount_directory(top, "@home")
                    destination = _mount_directory(root, "home")
                    _checked_mount(run, ["mount", "-t", "btrfs", "-o", "rw,subvol=@home",
                                         "--", partition.path, str(destination)])
                    mounts.append((destination, False))
            if esp:
                destination = _mount_directory(root, "boot/efi")
                # FAT permissions come from mount options. The signing verifier
                # must see a root-owned ESP without group/other write access.
                _checked_mount(run, ["mount", "-t", "vfat", "-o", "rw,uid=0,gid=0,umask=0077",
                                     "--", esp, str(destination)])
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
            if root_mount is not None and str(root_mount) not in failures:
                try:
                    root_mount.rmdir()
                except OSError:
                    # Never recursively remove a former mountpoint.
                    pass
            if failures:
                raise RuntimeError(tr("Could not unmount recovery filesystems: {details}").format(details=", ".join(failures)))


def finish_emergency_shell(path: str, identity: str, *, run: Run = subprocess.run) -> dict:
    """Wait for the terminal's helper to finish unmounting, then verify release.

    A terminal process can exit before its privileged helper. Never equate the
    vanished window with a released disk, or force-unmount a busy filesystem.
    """
    deadline = time.monotonic() + 90
    while _emergency_shell_running(path, identity):
        if time.monotonic() >= deadline:
            raise RuntimeError(tr("The emergency terminal is still running. Close it normally and try again."))
        time.sleep(0.2)
    partition = resolve_target(path, identity, run=run)
    if partition.mountpoints:
        raise RuntimeError(tr("Recovery filesystems are still in use. Close programs using the disk and try again."))
    return {"schema": 1, "released": True}


def _emergency_shell_running(path: str, identity: str) -> bool:
    for entry in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = entry.read_bytes().rstrip(b"\0").split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        helpers = (b"/usr/libexec/anduinos-rescue-center-helper",
                   b"/usr/libexec/anduinos-rescue-center-live-helper")
        for helper in helpers:
            if helper in arguments:
                tail = arguments[arguments.index(helper) + 1:]
                if tail == [b"shell", os.fsencode(path), os.fsencode(identity)]:
                    return True
    return False


def _restore_shell_signals() -> None:
    # Only the supervisor survives a terminal hangup; the interactive shell and
    # ordinary commands must retain their normal terminal-close behaviour.
    signal.signal(signal.SIGHUP, signal.SIG_DFL)


def emergency_shell(path: str, identity: str, *, run: Run = subprocess.run) -> int:
    """Attach an expert shell to the selected system from a Live terminal."""
    from .live import is_trusted_live_environment

    if not is_trusted_live_environment() or not os.isatty(0) or not os.isatty(1):
        raise RuntimeError(tr("An interactive AnduinOS Live terminal is required"))
    previous_hangup = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        with opened_system(path, identity, mount_home=True, run=run) as (root, partition):
            shell = "/bin/bash" if (root / "bin/bash").is_file() else "/bin/sh"
            print(f"\nOffline AnduinOS: {partition.path} ({partition.filesystem})")
            print(tr("This is a root shell in the selected offline system. Type exit to unmount it.\n"), flush=True)
            result = run(
                ["chroot", str(root), shell], check=False,
                preexec_fn=_restore_shell_signals,
                env={"HOME": "/root", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "TERM": os.environ.get("TERM", "xterm-256color")},
            )
            return result.returncode
    finally:
        signal.signal(signal.SIGHUP, previous_hangup)
