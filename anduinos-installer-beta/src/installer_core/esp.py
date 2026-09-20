"""Read-only shared-ESP and EFI-variable preflight inspection."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .storage_inventory import PartitionInventory


MIB = 1024 * 1024
GUIDED_ESP_MINIMUM_FREE_BYTES = 64 * MIB
EFI_BOOT_ENTRY_RE = re.compile(
    r"^Boot(?P<number>[0-9A-Fa-f]{4})\*?\s+(?P<label>.*?)\s+"
    r"HD\(\d+,GPT,(?P<partuuid>[^,]+),[^)]*\)/"
    r"(?:File\()?(?P<loader>\\[^)\s]+)\)?"
)
EFI_BOOT_ORDER_RE = re.compile(
    r"^BootOrder:\s*(?P<order>[0-9A-Fa-f]{4}(?:,[0-9A-Fa-f]{4})*)\s*$"
)


@dataclass(frozen=True)
class EspReuseInspection:
    partuuid: str
    filesystem_uuid: str
    healthy: bool
    free_bytes: int
    reason: str = ""
    preserved_entries: tuple["EspTreeEntry", ...] = ()
    vendor_entries: tuple["EspTreeEntry", ...] = ()


@dataclass(frozen=True)
class EspTreeEntry:
    relative_path: str
    kind: str
    size_bytes: int
    sha256: str = ""


@dataclass(frozen=True)
class NvramInspection:
    available: bool
    reason: str = ""


def inspect_esp_for_reuse(
    partition: PartitionInventory,
    runner: CommandRunner,
    *,
    scratch_root: Path = Path("/run/anduinos-installer"),
    statvfs: Callable[[str], os.statvfs_result] = os.statvfs,
) -> EspReuseInspection:
    """Check FAT consistency and capacity without modifying the ESP."""

    if not partition.is_efi_filesystem_candidate:
        raise ValueError("Selected partition is not a FAT EFI System Partition")
    if partition.mountpoints:
        raise ValueError("Selected EFI System Partition is already mounted")
    if not partition.identity.partuuid or not partition.filesystem_uuid:
        raise ValueError("Selected EFI System Partition has no stable identity")

    runner.require_commands(("fsck.fat", "mount", "umount"))
    check = runner.run(
        ("fsck.fat", "-n", partition.identity.path),
        check=False,
        timeout=120,
        log_output=False,
    )
    if check.returncode != 0:
        reason = check.stderr.strip() or check.stdout.strip()
        return EspReuseInspection(
            partuuid=partition.identity.partuuid,
            filesystem_uuid=partition.filesystem_uuid,
            healthy=False,
            free_bytes=0,
            reason=reason or "FAT consistency check failed",
        )

    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="esp-check-",
        dir=scratch_root,
    ) as directory:
        runner.run(
            (
                "mount",
                "--read-only",
                "--types",
                "vfat",
                "--options",
                "nosuid,nodev,noexec",
                partition.identity.path,
                directory,
            ),
            timeout=30,
        )
        try:
            status = statvfs(directory)
            free_bytes = status.f_bavail * status.f_frsize
            preserved_entries = capture_preserved_esp_tree(Path(directory))
            vendor_entries = capture_esp_vendor_tree(Path(directory))
        finally:
            runner.run(("umount", directory), timeout=30)

    return EspReuseInspection(
        partuuid=partition.identity.partuuid,
        filesystem_uuid=partition.filesystem_uuid,
        healthy=True,
        free_bytes=free_bytes,
        preserved_entries=preserved_entries,
        vendor_entries=vendor_entries,
    )


def capture_preserved_esp_tree(root: Path) -> tuple[EspTreeEntry, ...]:
    """Hash everything except the installer-owned EFI/AnduinOS subtree."""

    if not root.is_dir():
        raise RuntimeError(f"EFI System Partition is not mounted: {root}")
    entries: list[EspTreeEntry] = []
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        directories[:] = sorted(
            name
            for name in directories
            if not _is_vendor_path((parent / name).relative_to(root))
        )
        for name in directories:
            path = parent / name
            if path.is_symlink():
                raise RuntimeError(f"Unexpected symlink on ESP: {path}")
            entries.append(
                EspTreeEntry(
                    relative_path=path.relative_to(root).as_posix(),
                    kind="directory",
                    size_bytes=0,
                )
            )
        for name in sorted(files):
            path = parent / name
            relative = path.relative_to(root)
            if _is_vendor_path(relative):
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"Unexpected non-file on ESP: {path}")
            size_bytes, digest = _hash_file(path)
            entries.append(
                EspTreeEntry(
                    relative_path=relative.as_posix(),
                    kind="file",
                    size_bytes=size_bytes,
                    sha256=digest,
                )
            )
    return tuple(sorted(entries, key=lambda item: item.relative_path))


def verify_preserved_esp_tree(
    expected: tuple[EspTreeEntry, ...],
    mounted_esp: Path,
) -> None:
    actual = capture_preserved_esp_tree(mounted_esp)
    if actual != expected:
        raise RuntimeError(
            "Shared EFI System Partition changed outside EFI/AnduinOS"
        )


def capture_esp_vendor_tree(root: Path) -> tuple[EspTreeEntry, ...]:
    """Capture only the installer-owned EFI/AnduinOS subtree."""

    vendor = root / "EFI/AnduinOS"
    if not vendor.exists():
        return ()
    if not vendor.is_dir() or vendor.is_symlink():
        raise RuntimeError(f"Invalid AnduinOS vendor directory: {vendor}")
    entries: list[EspTreeEntry] = []
    for directory, directories, files in os.walk(vendor, followlinks=False):
        parent = Path(directory)
        directories[:] = sorted(directories)
        for name in directories:
            path = parent / name
            if path.is_symlink():
                raise RuntimeError(f"Unexpected symlink on ESP: {path}")
            entries.append(
                EspTreeEntry(
                    relative_path=path.relative_to(root).as_posix(),
                    kind="directory",
                    size_bytes=0,
                )
            )
        for name in sorted(files):
            path = parent / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"Unexpected non-file on ESP: {path}")
            size_bytes, digest = _hash_file(path)
            entries.append(
                EspTreeEntry(
                    relative_path=path.relative_to(root).as_posix(),
                    kind="file",
                    size_bytes=size_bytes,
                    sha256=digest,
                )
            )
    return tuple(sorted(entries, key=lambda item: item.relative_path))


def _is_vendor_path(relative: Path) -> bool:
    parts = tuple(item.casefold() for item in relative.parts)
    return len(parts) >= 2 and parts[:2] == ("efi", "anduinos")


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
    return size_bytes, digest.hexdigest()


def inspect_nvram(runner: CommandRunner) -> NvramInspection:
    """Confirm that UEFI boot variables are readable before storage writes."""

    runner.require_commands(("efibootmgr",))
    result = runner.run(
        ("efibootmgr", "--verbose"),
        check=False,
        timeout=30,
        log_output=False,
    )
    if result.returncode == 0:
        return NvramInspection(available=True)
    reason = result.stderr.strip() or result.stdout.strip()
    return NvramInspection(
        available=False,
        reason=reason or "UEFI boot variables are unavailable",
    )


def verify_nvram_entry(
    output: str,
    *,
    label: str,
    partuuid: str,
    loader: str,
    require_first: bool = False,
) -> str:
    """Require an exact GPT partition and loader in efibootmgr output."""

    expected_uuid = partuuid.strip("{}").lower()
    expected_loader = loader.replace("/", "\\").lower()
    matching_number = ""
    for line in output.splitlines():
        match = EFI_BOOT_ENTRY_RE.match(line.strip())
        if match is None or match.group("label") != label:
            continue
        actual_uuid = match.group("partuuid").strip("{}").lower()
        actual_loader = match.group("loader").replace("/", "\\").lower()
        if actual_uuid == expected_uuid and actual_loader == expected_loader:
            matching_number = match.group("number").upper()
            break
    if not matching_number:
        raise RuntimeError(
            "The AnduinOS UEFI boot entry was not created for the selected ESP"
        )
    if require_first:
        order = nvram_boot_order(output)
        if not order or order[0] != matching_number:
            raise RuntimeError(
                "The AnduinOS UEFI boot entry is not first in BootOrder"
            )
    return matching_number


def nvram_boot_order(output: str) -> tuple[str, ...]:
    """Parse the firmware boot order reported by efibootmgr."""

    for line in output.splitlines():
        match = EFI_BOOT_ORDER_RE.match(line.strip())
        if match:
            return tuple(
                item.upper() for item in match.group("order").split(",")
            )
    raise RuntimeError(
        "UEFI BootOrder was not reported by efibootmgr"
    )
