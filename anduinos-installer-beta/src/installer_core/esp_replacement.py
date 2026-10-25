"""Backup and replace only an explicitly authorized AnduinOS EFI subtree."""

import hashlib
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

from .esp import (
    EspReuseInspection, capture_esp_vendor_tree, esp_vendor_digest,
    validate_esp_replacement,
)


def _sync_file(path):
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_archive(archive, authorization):
    with tempfile.TemporaryDirectory(prefix="verify-", dir=archive.parent) as verify:
        with tarfile.open(archive, "r") as saved:
            saved.extractall(verify, filter="data")
        if esp_vendor_digest(capture_esp_vendor_tree(Path(verify))) != authorization.vendor_digest:
            raise RuntimeError("AnduinOS EFI backup verification failed")


def _verify_device_identity(device, authorization, runner):
    identity = dict(line.split('=', 1) for line in runner.run(
        ('blkid', '-o', 'export', device), timeout=10).stdout.splitlines() if '=' in line)
    if (identity.get('PARTUUID', '').lower() != authorization.partuuid.lower()
            or identity.get('UUID', '').upper() != authorization.filesystem_uuid.upper()):
        raise RuntimeError("ESP identity changed; refusing EFI replacement")


def backup_esp_replacement(partition, authorization, runner, *,
                           backup_root=Path("/var/backups/anduinos-installer")):
    """Run before partition writes; any backup failure aborts installation."""
    _verify_device_identity(partition.identity.path, authorization, runner)
    backup_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="esp-", dir=backup_root))
    archive = directory / "anduinos-efi.tar"
    with tempfile.TemporaryDirectory(prefix="esp-read-", dir=directory) as mount:
        runner.run(("mount", "--read-only", "--types", "vfat", "--options",
                    "nosuid,nodev,noexec", partition.identity.path, mount), timeout=30)
        try:
            root = Path(mount)
            entries = capture_esp_vendor_tree(root)
            validate_esp_replacement(EspReuseInspection(
                partition.identity.partuuid, partition.filesystem_uuid, True, 0,
                vendor_entries=entries), authorization)
            with tarfile.open(archive, "w") as output:
                for entry in entries:
                    output.add(root / entry.relative_path,
                               arcname=entry.relative_path, recursive=False)
            # Check the archive itself, not merely the source directory.
            _verify_archive(archive, authorization)
            manifest = directory / "recovery.txt"
            manifest.write_text(
                "AnduinOS EFI boot-file backup (not a system/disk rollback).\n"
                f"Original ESP: {partition.identity.path}\n"
                f"PARTUUID: {authorization.partuuid}\n"
                f"Filesystem UUID: {authorization.filesystem_uuid}\n"
                f"Vendor fingerprint: {authorization.vendor_digest}\n"
                "The archive contains only the original EFI/AnduinOS subtree.\n"
                "To recover the old boot files, boot a Live system, identify and mount\n"
                "the original ESP by these UUIDs, and restore this archive there.\n"
                "Do not format the ESP or modify other vendors' boot files.\n"
                "This does not restore deleted root partitions or firmware boot order.\n",
                encoding="utf-8")
            _sync_file(archive)
            _sync_file(manifest)
            _sync_directory(directory)
            _sync_directory(backup_root)
        finally:
            runner.run(("umount", mount), timeout=30)
    return directory


def replace_esp_vendor(context, target, runner):
    """Persist recovery files and recheck consent immediately before removal."""
    authorization = context.plan.boot.esp_replacement
    if authorization is None:
        return
    source = context.values.get("esp_replacement_backup")
    if not isinstance(source, Path) or not (source / "anduinos-efi.tar").is_file():
        raise RuntimeError("Verified AnduinOS EFI backup is missing")
    device = context.values.get('partition_devices', {}).get('efi-system')
    if not device:
        raise RuntimeError("Replacement ESP device is missing")
    _verify_device_identity(device, authorization, runner)
    root = target / "boot/efi"
    entries = capture_esp_vendor_tree(root)
    validate_esp_replacement(EspReuseInspection(
        authorization.partuuid, authorization.filesystem_uuid, True, 0,
        vendor_entries=entries), authorization)
    backup_root = target / "var/backups/anduinos-installer"
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="esp-", dir=backup_root))
    for name in ("anduinos-efi.tar", "recovery.txt"):
        shutil.copyfile(source / name, destination / name)
        _sync_file(destination / name)
    # A failed/power-interrupted copy must never authorize removal.
    if _digest(source / "anduinos-efi.tar") != _digest(destination / "anduinos-efi.tar"):
        raise RuntimeError("Installed-system AnduinOS EFI backup verification failed")
    _verify_archive(destination / "anduinos-efi.tar", authorization)
    _sync_directory(destination)
    _sync_directory(backup_root)
    context.log(f"Old AnduinOS EFI backup in the installed system: /{destination.relative_to(target)}")
    if esp_vendor_digest(capture_esp_vendor_tree(root)) != authorization.vendor_digest:
        raise RuntimeError("AnduinOS EFI files changed before replacement; old files were not removed")
    # Only a validated two-component vendor directory is ever removed.
    vendor = next(e for e in entries if len(Path(e.relative_path).parts) == 2)
    if vendor.kind != "directory" or tuple(p.casefold() for p in Path(vendor.relative_path).parts) != ("efi", "anduinos"):
        raise RuntimeError("Invalid AnduinOS EFI replacement directory")
    shutil.rmtree(root / vendor.relative_path)
    context.log("Replacing explicitly authorized EFI/AnduinOS boot files; other EFI directories are preserved")
