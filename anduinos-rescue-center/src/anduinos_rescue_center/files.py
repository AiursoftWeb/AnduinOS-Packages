"""Safe read-only browsing and bounded export from an offline system."""

from __future__ import annotations

import os
import pwd
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .storage import inspect_mounted_filesystem, mounted_readonly, resolve_target


Run = Callable[..., subprocess.CompletedProcess[str]]
MAX_ENTRIES = 5000


def list_files(
    device: str,
    identity: str,
    relative: str,
    *,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> dict[str, object]:
    partition = resolve_target(device, identity, run=run)
    with mounted_readonly(
        partition.path, partition.filesystem, run=run, mount_base=mount_base
    ) as top:
        if inspect_mounted_filesystem(top, partition.filesystem).os_kind != "anduinos":
            raise RuntimeError("The selected partition is not an AnduinOS installation")
        base, logical = logical_path(top, partition.filesystem, relative)
        if not base.is_dir():
            raise RuntimeError("The selected location is not a directory")
        entries: list[dict[str, object]] = []
        with os.scandir(base) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_ENTRIES:
                    break
                metadata = entry.stat(follow_symlinks=False)
                mode = metadata.st_mode
                kind = (
                    "directory" if stat.S_ISDIR(mode)
                    else "file" if stat.S_ISREG(mode)
                    else "symlink" if stat.S_ISLNK(mode)
                    else "other"
                )
                child = str(PurePosixPath(logical) / entry.name)
                entries.append({
                    "name": entry.name,
                    "path": child,
                    "kind": kind,
                    "size": metadata.st_size,
                    "modified": int(metadata.st_mtime),
                })
        entries.sort(key=lambda item: (item["kind"] != "directory", str(item["name"]).casefold()))
        return {
            "schema": 1,
            "path": logical,
            "parent": _logical_parent(logical),
            "entries": entries,
            "truncated": len(entries) == MAX_ENTRIES,
        }


def export_file(
    device: str,
    identity: str,
    relative: str,
    destination_directory: str,
    *,
    caller_uid: int,
    run: Run = subprocess.run,
    mount_base: Path = Path("/run/anduinos-rescue-center"),
) -> str:
    if caller_uid <= 0:
        raise RuntimeError("Could not identify the desktop user")
    partition = resolve_target(device, identity, run=run)
    destination = allowed_destination(Path(destination_directory), caller_uid)
    with mounted_readonly(
        partition.path, partition.filesystem, run=run, mount_base=mount_base
    ) as top:
        if inspect_mounted_filesystem(top, partition.filesystem).os_kind != "anduinos":
            raise RuntimeError("The selected partition is not an AnduinOS installation")
        source, logical = logical_path(top, partition.filesystem, relative)
        if logical == ".":
            raise RuntimeError("Export a file or folder instead of the whole system")
        metadata = source.lstat()
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
            raise RuntimeError("Symbolic links and special files cannot be exported")
        target = destination / source.name
        if target.exists() or target.is_symlink():
            raise RuntimeError("A file with this name already exists at the destination")
        account = pwd.getpwuid(caller_uid)
        try:
            _copy_safe(source, target, account.pw_uid, account.pw_gid)
            sync = run(
                ["sync", "-f", str(target)], capture_output=True, text=True,
                timeout=120, check=False,
            )
            if sync.returncode != 0:
                raise RuntimeError((sync.stderr or sync.stdout).strip() or "Could not sync exported data")
        except Exception:
            _remove_created(target)
            raise
        return str(target)


def logical_path(top: Path, filesystem: str, relative: str) -> tuple[Path, str]:
    logical = _normalize_relative(relative)
    root = top / "@root" if filesystem == "btrfs" and (top / "@root").is_dir() else top
    parts = () if logical == "." else PurePosixPath(logical).parts
    if filesystem == "btrfs" and parts and parts[0] == "home" and (top / "@home").is_dir():
        base = top / "@home"
        remainder = parts[1:]
    else:
        base = root
        remainder = parts
    canonical_base = base.resolve(strict=True)
    candidate = canonical_base.joinpath(*remainder).resolve(strict=True)
    if not candidate.is_relative_to(canonical_base):
        raise RuntimeError("The selected path escapes the offline system")
    return candidate, logical


def allowed_destination(path: Path, caller_uid: int) -> Path:
    account = pwd.getpwuid(caller_uid)
    candidate = path.resolve(strict=True)
    roots = [Path(account.pw_dir).resolve(strict=True)]
    for prefix in (Path("/media") / account.pw_name, Path("/run/media") / account.pw_name):
        try:
            roots.append(prefix.resolve(strict=True))
        except OSError:
            pass
    if not candidate.is_dir() or not any(candidate.is_relative_to(root) for root in roots):
        raise RuntimeError("Choose a folder in your Home directory or on your removable media")
    return candidate


def _normalize_relative(value: str) -> str:
    if not value or value == "/":
        return "."
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or len(value) > 4096:
        raise ValueError("Invalid offline file path")
    return str(path)


def _logical_parent(value: str) -> str | None:
    if value == ".":
        return None
    parent = str(PurePosixPath(value).parent)
    return "." if parent in {"", "."} else parent


def _copy_safe(source: Path, target: Path, uid: int, gid: int) -> None:
    metadata = source.lstat()
    if stat.S_ISREG(metadata.st_mode):
        source_fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            target_fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                metadata.st_mode & 0o777,
            )
            try:
                with os.fdopen(source_fd, "rb", closefd=False) as source_file, os.fdopen(
                    target_fd, "wb", closefd=False
                ) as target_file:
                    shutil.copyfileobj(source_file, target_file, length=1024 * 1024)
                    target_file.flush()
                    os.fsync(target_fd)
                os.fchown(target_fd, uid, gid)
            finally:
                os.close(target_fd)
        finally:
            os.close(source_fd)
        return
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Cannot export special file: {source.name}")
    target.mkdir(mode=metadata.st_mode & 0o777)
    os.chown(target, uid, gid, follow_symlinks=False)
    with os.scandir(source) as iterator:
        for entry in iterator:
            _copy_safe(Path(entry.path), target / entry.name, uid, gid)


def _remove_created(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass
