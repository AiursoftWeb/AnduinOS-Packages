"""Read-only storage discovery for offline AnduinOS installations."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator

from .live import is_live_environment
from .model import Disk, Inventory, Partition
from .os_release import read_os_release


Run = Callable[..., subprocess.CompletedProcess[str]]
Inspect = Callable[[str, str], "SystemIdentity"]


@dataclass(frozen=True)
class SystemIdentity:
    os_kind: str = "unknown"
    os_name: str = ""
    os_version: str = ""
    hostname: str = ""
    btrfs_layout: bool = False
    error: str = ""


def probe_inventory(
    *,
    run: Run = subprocess.run,
    inspect: Inspect | None = None,
    live: bool | None = None,
) -> Inventory:
    """Enumerate disks and positively identify installed operating systems."""

    command = [
        "lsblk", "--json", "--bytes", "--paths", "--tree", "--output",
        "PATH,SIZE,MODEL,SERIAL,TYPE,RM,RO,TRAN,MAJ:MIN,UUID,PARTUUID,FSTYPE,LABEL,MOUNTPOINTS",
    ]
    result = run(
        command,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not enumerate storage")
    try:
        roots = json.loads(result.stdout)["blockdevices"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("lsblk returned invalid storage data") from error
    if not isinstance(roots, list):
        raise RuntimeError("lsblk returned invalid storage data")

    inspector = inspect or (lambda path, fstype: inspect_partition(path, fstype, run=run))
    live_sources = _live_sources(roots)
    disks: list[Disk] = []
    for root in roots:
        if not isinstance(root, dict) or root.get("type") != "disk":
            continue
        disk_path = _string(root.get("path"))
        if not disk_path.startswith("/dev/"):
            continue
        partitions: list[Partition] = []
        for node in _descendants(root):
            if node.get("type") != "part":
                continue
            path = _string(node.get("path"))
            filesystem = _string(node.get("fstype")).lower()
            mountpoints = tuple(
                item for item in (node.get("mountpoints") or ()) if isinstance(item, str) and item
            )
            identity = _identity(
                path,
                _string(node.get("maj:min")),
                _string(node.get("uuid")),
                _string(node.get("partuuid")),
                _integer(node.get("size")),
            )
            partition = Partition(
                path=path,
                parent=disk_path,
                size_bytes=_integer(node.get("size")),
                filesystem=filesystem,
                filesystem_uuid=_string(node.get("uuid")),
                label=_string(node.get("label")),
                partuuid=_string(node.get("partuuid")),
                mountpoints=mountpoints,
                identity=identity,
                active_system="/" in mountpoints,
            )
            if filesystem in {"btrfs", "ext2", "ext3", "ext4", "xfs", "ntfs", "ntfs3"}:
                try:
                    found = inspector(path, filesystem)
                except Exception as error:  # A bad disk must not hide healthy installations.
                    found = SystemIdentity(error=str(error))
                partition = replace(
                    partition,
                    os_kind=found.os_kind,
                    os_name=found.os_name,
                    os_version=found.os_version,
                    hostname=found.hostname,
                    btrfs_layout=found.btrfs_layout,
                    probe_error=found.error,
                )
            partitions.append(partition)
        disks.append(
            Disk(
                path=disk_path,
                model=_string(root.get("model")).strip(),
                serial=_string(root.get("serial")).strip(),
                size_bytes=_integer(root.get("size")),
                transport=_string(root.get("tran")).lower(),
                removable=_boolean(root.get("rm")),
                read_only=_boolean(root.get("ro")),
                live_medium=disk_path in live_sources,
                identity=_identity(
                    disk_path,
                    _string(root.get("maj:min")),
                    "",
                    _string(root.get("serial")),
                    _integer(root.get("size")),
                ),
                partitions=tuple(partitions),
            )
        )
    disks.sort(key=lambda disk: disk.identity)
    return Inventory(
        tuple(disks),
        is_live_environment() if live is None else live,
        probe_secure_boot(run=run),
    )


def inspect_partition(
    path: str,
    filesystem: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> SystemIdentity:
    """Mount one block device without replaying its journal and inspect it."""

    _require_block_device(path)
    with mounted_readonly(path, filesystem, run=run, mount_base=mount_base) as mountpoint:
        return inspect_mounted_filesystem(mountpoint, filesystem)


@contextmanager
def mounted_readonly(
    path: str,
    filesystem: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> Iterator[Path]:
    """Mount a validated block device read-only and always unmount safely."""

    _require_block_device(path)
    mount_base.mkdir(mode=0o700, parents=True, exist_ok=True)
    mountpoint = Path(tempfile.mkdtemp(prefix="probe-", dir=mount_base))
    options = {
        # New kernels accept the no-log-replay safeguard through Btrfs's
        # rescue option namespace.  The former standalone nologreplay spelling
        # is rejected by the kernel shipped in the AnduinOS Live image.
        "btrfs": "ro,rescue=nologreplay,subvolid=5",
        "ext2": "ro,noload",
        "ext3": "ro,noload",
        "ext4": "ro,noload",
        "xfs": "ro,norecovery",
        "ntfs": "ro",
        "ntfs3": "ro",
    }.get(filesystem, "ro")
    mounted = False
    try:
        result = run(
            ["mount", "-t", filesystem, "-o", options, "--", path, str(mountpoint)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "Mount failed")
        mounted = True
        yield mountpoint
    finally:
        if mounted:
            unmount = run(
                ["umount", "--", str(mountpoint)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if unmount.returncode != 0:
                raise RuntimeError(
                    (unmount.stderr or unmount.stdout).strip()
                    or f"Could not unmount {mountpoint}"
                )
        try:
            mountpoint.rmdir()
        except OSError:
            # Never recursively remove this path. If it is unexpectedly still
            # mounted, recursive deletion could destroy the inspected system.
            pass


@contextmanager
def mounted_writable(
    path: str,
    filesystem: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> Iterator[Path]:
    """Mount an explicitly selected offline target for a bounded mutation."""

    if filesystem not in {"btrfs", "ext2", "ext3", "ext4", "xfs"}:
        raise RuntimeError("This filesystem is not supported for repair operations")
    _require_block_device(path)
    mount_base.mkdir(mode=0o700, parents=True, exist_ok=True)
    mountpoint = Path(tempfile.mkdtemp(prefix="repair-", dir=mount_base))
    options = "rw,subvolid=5" if filesystem == "btrfs" else "rw"
    mounted = False
    try:
        result = run(
            ["mount", "-t", filesystem, "-o", options, "--", path, str(mountpoint)],
            capture_output=True, text=True, timeout=30, check=False,
            env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout).strip() or "Mount failed")
        mounted = True
        yield mountpoint
    finally:
        if mounted:
            unmount = run(
                ["umount", "--", str(mountpoint)], capture_output=True,
                text=True, timeout=30, check=False,
            )
            if unmount.returncode != 0:
                raise RuntimeError(
                    (unmount.stderr or unmount.stdout).strip()
                    or f"Could not unmount {mountpoint}"
                )
        try:
            mountpoint.rmdir()
        except OSError:
            pass


def inspect_mounted_filesystem(mountpoint: Path, filesystem: str) -> SystemIdentity:
    top = mountpoint
    root = top / "@root" if filesystem == "btrfs" and (top / "@root").is_dir() else top
    release_path = _safe_regular_file(root, "usr/lib/os-release")
    if release_path is None:
        release_path = _safe_regular_file(root, "etc/os-release")
    release = read_os_release(release_path) if release_path else {}
    os_id = release.get("ID", "").lower()
    hostname_path = _safe_regular_file(root, "etc/hostname")
    hostname = _read_small_text(hostname_path) if hostname_path else ""
    if os_id == "anduinos":
        return SystemIdentity(
            os_kind="anduinos",
            os_name=release.get("PRETTY_NAME") or release.get("NAME", "AnduinOS"),
            os_version=release.get("VERSION_ID", ""),
            hostname=hostname,
            btrfs_layout=filesystem == "btrfs" and root != top,
        )
    if (root / "Windows/System32").is_dir() and (root / "ProgramData").is_dir():
        return SystemIdentity(os_kind="windows", os_name="Windows")
    if os_id:
        return SystemIdentity(
            os_kind="linux",
            os_name=release.get("PRETTY_NAME") or release.get("NAME", "Linux"),
            os_version=release.get("VERSION_ID", ""),
            hostname=hostname,
        )
    return SystemIdentity()


def inspect_target(
    path: str,
    identity: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> dict[str, object]:
    """Return bounded system and account information for one offline target."""

    partition = resolve_target(path, identity, run=run)
    if partition.active_system:
        raise RuntimeError("The currently running system cannot be a rescue target")
    with mounted_readonly(
        partition.path, partition.filesystem, run=run, mount_base=mount_base
    ) as top:
        return inspect_offline_root(top, partition)


def inspect_offline_root(top: Path, partition: Partition) -> dict[str, object]:
    root = top / "@root" if partition.filesystem == "btrfs" and (top / "@root").is_dir() else top
    detected = inspect_mounted_filesystem(top, partition.filesystem)
    if detected.os_kind != "anduinos":
        raise RuntimeError("The selected partition is not an AnduinOS installation")
    return {
        "schema": 1,
        "target": partition.to_dict(),
        "system": {
            "name": detected.os_name,
            "version": detected.os_version,
            "hostname": detected.hostname,
            "filesystem": partition.filesystem,
            "btrfs_layout": detected.btrfs_layout,
            "kernels": _kernel_versions(root),
        },
        "users": _local_users(root),
    }


def resolve_target(path: str, identity: str, *, run: Run = subprocess.run) -> Partition:
    """Re-probe a target and bind its current device identity to the UI token."""

    result = run(
        [
            "lsblk", "--json", "--bytes", "--paths", "--tree", "--output",
            "PATH,SIZE,TYPE,MAJ:MIN,UUID,PARTUUID,FSTYPE,LABEL,MOUNTPOINTS",
        ],
        capture_output=True, text=True, timeout=15, check=False,
        env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not re-check the selected disk")
    try:
        roots = json.loads(result.stdout)["blockdevices"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("lsblk returned invalid storage data") from error
    for root in roots if isinstance(roots, list) else ():
        if not isinstance(root, dict):
            continue
        parent = _string(root.get("path"))
        for node in _descendants(root):
            if node.get("type") != "part" or node.get("path") != path:
                continue
            current = _identity(
                path, _string(node.get("maj:min")), _string(node.get("uuid")),
                _string(node.get("partuuid")), _integer(node.get("size")),
            )
            if current != identity:
                raise RuntimeError("The selected partition changed after it was scanned")
            mounts = tuple(
                item for item in (node.get("mountpoints") or ())
                if isinstance(item, str) and item
            )
            return Partition(
                path=path,
                parent=parent,
                size_bytes=_integer(node.get("size")),
                filesystem=_string(node.get("fstype")).lower(),
                filesystem_uuid=_string(node.get("uuid")),
                label=_string(node.get("label")),
                partuuid=_string(node.get("partuuid")),
                mountpoints=mounts,
                identity=current,
                active_system="/" in mounts,
            )
    raise RuntimeError("The selected partition is no longer available")


def probe_secure_boot(*, run: Run = subprocess.run) -> str:
    try:
        result = run(
            ["mokutil", "--sb-state"], capture_output=True, text=True,
            timeout=10, check=False,
            env=dict(os.environ, LC_ALL="C", LANGUAGE="C"),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    output = f"{result.stdout}\n{result.stderr}".lower()
    if "secureboot enabled" in output or "secure boot enabled" in output:
        return "enabled"
    if "secureboot disabled" in output or "secure boot disabled" in output:
        return "disabled"
    if "not supported" in output or "efi variables are not supported" in output:
        return "unsupported"
    return "unknown"


def _require_block_device(path: str) -> None:
    if not path.startswith("/dev/") or ".." in Path(path).parts:
        raise ValueError("Invalid block-device path")
    try:
        metadata = os.stat(path, follow_symlinks=True)
    except OSError as error:
        raise ValueError("Block device is unavailable") from error
    if not stat.S_ISBLK(metadata.st_mode):
        raise ValueError("Target is not a block device")


def _descendants(node: dict) -> list[dict]:
    found: list[dict] = []
    pending = list(node.get("children") or ())
    while pending:
        child = pending.pop(0)
        if not isinstance(child, dict):
            continue
        found.append(child)
        pending.extend(child.get("children") or ())
    return found


def _live_sources(roots: list[object]) -> set[str]:
    markers = {"/cdrom", "/run/live/medium", "/run/initramfs/live", "/run/anduinos-live/rootfs.squashfs"}
    sources: set[str] = set()
    for root in roots:
        if not isinstance(root, dict):
            continue
        for node in [root, *_descendants(root)]:
            if markers.intersection(node.get("mountpoints") or ()):
                path = _string(root.get("path"))
                if path:
                    sources.add(path)
    return sources


def _identity(path: str, major_minor: str, uuid: str, partuuid: str, size: int) -> str:
    value = "\0".join((path, major_minor, uuid, partuuid, str(size)))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_small_text(path: Path) -> str:
    try:
        if path.stat().st_size > 4096:
            return ""
        return path.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError):
        return ""


def _safe_regular_file(root: Path, relative: str) -> Path | None:
    try:
        canonical_root = root.resolve(strict=True)
        candidate = (root / relative).resolve(strict=True)
        if not candidate.is_relative_to(canonical_root) or not candidate.is_file():
            return None
        return candidate
    except OSError:
        return None


def _kernel_versions(root: Path) -> list[str]:
    boot = root / "boot"
    try:
        canonical_root = root.resolve(strict=True)
        canonical_boot = boot.resolve(strict=True)
        if not canonical_boot.is_relative_to(canonical_root):
            return []
        names = sorted(
            path.name.removeprefix("vmlinuz-")
            for path in canonical_boot.glob("vmlinuz-*")
            if path.is_file() and path.name != "vmlinuz"
        )
    except OSError:
        return []
    return names[-20:]


def _local_users(root: Path) -> list[dict[str, object]]:
    passwd_path = _safe_regular_file(root, "etc/passwd")
    if passwd_path is None:
        return []
    uid_min, uid_max = 1000, 60000
    login_defs = _safe_regular_file(root, "etc/login.defs")
    if login_defs:
        for line in _read_bounded_text(login_defs, 1024 * 1024).splitlines():
            words = line.split()
            if len(words) == 2 and words[0] in {"UID_MIN", "UID_MAX"}:
                try:
                    if words[0] == "UID_MIN":
                        uid_min = int(words[1])
                    else:
                        uid_max = int(words[1])
                except ValueError:
                    pass
    locked: dict[str, bool] = {}
    shadow_path = _safe_regular_file(root, "etc/shadow")
    if shadow_path:
        for line in _read_bounded_text(shadow_path, 4 * 1024 * 1024).splitlines():
            fields = line.split(":", 2)
            if len(fields) >= 2:
                locked[fields[0]] = fields[1].startswith(("!", "*"))
    users: list[dict[str, object]] = []
    for line in _read_bounded_text(passwd_path, 4 * 1024 * 1024).splitlines():
        fields = line.split(":")
        if len(fields) != 7:
            continue
        name, _password, uid_text, _gid, gecos, home, shell = fields
        try:
            uid = int(uid_text)
        except ValueError:
            continue
        if name != "root" and not (uid_min <= uid <= uid_max and home.startswith("/home/")):
            continue
        users.append({
            "name": name,
            "uid": uid,
            "display_name": gecos.split(",", 1)[0],
            "home": home,
            "shell": shell,
            "locked": locked.get(name),
        })
    return sorted(users, key=lambda user: (user["uid"] != 0, user["uid"]))


def _read_bounded_text(path: Path, maximum: int) -> str:
    try:
        if path.stat().st_size > maximum:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _boolean(value: object) -> bool:
    return value is True or value == 1 or value == "1"
