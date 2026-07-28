"""Translate a validated layout into explicit storage commands.

This module is intentionally pure: command generation is unit-testable and
does not itself execute anything against a disk.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import PartitionLayout, PartitionSpec
from .model import Filesystem, InstallPlan
from .validation import validate_plan


@dataclass(frozen=True)
class StorageCommandPlan:
    partition: tuple[tuple[str, ...], ...]
    format: tuple[tuple[str, ...], ...]
    devices: dict[str, str]


def partition_path(disk: str, number: int) -> str:
    separator = "p" if disk.startswith(("/dev/nvme", "/dev/mmcblk")) else ""
    return f"{disk}{separator}{number}"


def build_storage_commands(
    plan: InstallPlan, layout: PartitionLayout
) -> StorageCommandPlan:
    validate_plan(plan)
    disk = plan.storage.disk.path
    partition_commands: list[tuple[str, ...]] = [
        ("parted", "--script", disk, "mklabel", layout.table)
    ]
    devices: dict[str, str] = {}

    for part in layout.partitions:
        end = f"{part.end_mib}MiB" if part.end_mib is not None else "100%"
        filesystem_hint = _parted_filesystem_hint(part)
        command = [
            "parted",
            "--script",
            disk,
            "unit",
            "MiB",
            "mkpart",
            part.name,
        ]
        if filesystem_hint:
            command.append(filesystem_hint)
        command.extend((f"{part.start_mib}MiB", end))
        partition_commands.append(tuple(command))
        for flag in part.flags:
            if flag == "swap":
                continue
            partition_commands.append(
                (
                    "parted",
                    "--script",
                    disk,
                    "set",
                    str(part.number),
                    flag,
                    "on",
                )
            )
        devices[part.name] = partition_path(disk, part.number)

    format_commands: list[tuple[str, ...]] = [
        ("mkfs.vfat", "-F", "32", "-n", "ANDUIN_EFI", devices["efi-system"]),
        ("mkswap", "-L", "AnduinOS-swap", devices["swap"]),
    ]
    root = devices["root"]
    if plan.storage.filesystem is Filesystem.BTRFS:
        format_commands.append(
            ("mkfs.btrfs", "--force", "--label", "AnduinOS", root)
        )
    else:
        format_commands.append(
            ("mkfs.ext4", "-F", "-L", "AnduinOS", root)
        )

    return StorageCommandPlan(
        partition=tuple(partition_commands),
        format=tuple(format_commands),
        devices=devices,
    )


def _parted_filesystem_hint(partition: PartitionSpec) -> str | None:
    return {
        "fat32": "fat32",
        "linux-swap": "linux-swap",
        "btrfs": "btrfs",
        "ext4": "ext4",
    }.get(partition.filesystem or "")

