"""Translate a validated layout into explicit storage commands.

This module is intentionally pure: command generation is unit-testable and
does not itself execute anything against a disk.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import PartitionLayout, PartitionSpec
from .model import Filesystem, InstallMode, InstallPlan
from .storage_graph import GraphFilesystem, StorageGraphAction
from .storage_inventory import StorageInventory
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
    ]
    if "swap" in devices:
        format_commands.append(
            ("mkswap", "-L", "AnduinOS-swap", devices["swap"])
        )
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


def build_guided_coexistence_storage_commands(
    plan: InstallPlan,
    inventory: StorageInventory,
) -> StorageCommandPlan:
    """Compile a freshly validated coexistence graph into fixed commands."""

    if plan.storage.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise ValueError("Plan is not guided coexistence")
    # Keep the import local: ordinary erase-disk validation imports this
    # module through storage planning.
    from .storage_graph_planning import validate_guided_coexistence_graph

    disk = validate_guided_coexistence_graph(plan, inventory)
    graph = plan.storage.graph
    assert graph is not None
    disk_path = disk.identity.path
    partition_commands: list[tuple[str, ...]] = []
    devices = {
        item.name: partition_path(disk_path, item.number)
        for item in graph.partitions
    }

    for part in graph.partitions:
        filesystem = next(
            item.filesystem
            for item in graph.filesystems
            if item.block_id == part.partition_id
        )
        filesystem_hint = _graph_parted_filesystem_hint(filesystem)
        command = [
            "parted",
            "--script",
            disk_path,
            "unit",
            "MiB",
            "mkpart",
            part.name,
        ]
        if filesystem_hint:
            command.append(filesystem_hint)
        if part.end_mib is None:
            raise RuntimeError("Coexistence partition has no bounded end")
        command.extend((f"{part.start_mib}MiB", f"{part.end_mib}MiB"))
        partition_commands.append(tuple(command))
        for flag in part.flags:
            if flag == "swap":
                continue
            partition_commands.append(
                (
                    "parted",
                    "--script",
                    disk_path,
                    "set",
                    str(part.number),
                    flag,
                    "on",
                )
            )

    boot_target = graph.boot_targets[0]
    if "efi-system" not in devices:
        existing_esp = next(
            (
                item
                for item in disk.partitions
                if _existing_partition_reference_id(
                    disk.identity.stable_id,
                    item.identity.partuuid,
                )
                == boot_target.efi_filesystem_id
            ),
            None,
        )
        if existing_esp is not None:
            devices["efi-system"] = existing_esp.identity.path
    expected_devices = {"efi-system", "root"}
    if plan.storage.swap_size_mib:
        expected_devices.add("swap")
    if set(devices) != expected_devices:
        raise RuntimeError("Coexistence graph did not resolve target devices")

    formatted_ids = {
        item.target_id
        for item in graph.operations
        if item.action is StorageGraphAction.FORMAT
    }
    declarations = {
        item.block_id: item
        for item in graph.filesystems
        if item.block_id in formatted_ids
    }
    format_commands: list[tuple[str, ...]] = []
    for part in graph.partitions:
        declaration = declarations.get(part.partition_id)
        if declaration is None:
            continue
        device = partition_path(disk_path, part.number)
        format_commands.append(_format_command(declaration.filesystem, device))

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


def _graph_parted_filesystem_hint(filesystem: GraphFilesystem) -> str:
    return {
        GraphFilesystem.VFAT: "fat32",
        GraphFilesystem.SWAP: "linux-swap",
        GraphFilesystem.BTRFS: "btrfs",
        GraphFilesystem.EXT4: "ext4",
    }[filesystem]


def _format_command(
    filesystem: GraphFilesystem,
    device: str,
) -> tuple[str, ...]:
    if filesystem is GraphFilesystem.VFAT:
        return ("mkfs.vfat", "-F", "32", "-n", "ANDUIN_EFI", device)
    if filesystem is GraphFilesystem.SWAP:
        return ("mkswap", "-L", "AnduinOS-swap", device)
    if filesystem is GraphFilesystem.BTRFS:
        return ("mkfs.btrfs", "--force", "--label", "AnduinOS", device)
    if filesystem is GraphFilesystem.EXT4:
        return ("mkfs.ext4", "-F", "-L", "AnduinOS", device)
    raise RuntimeError(f"Unsupported coexistence filesystem: {filesystem}")


def _existing_partition_reference_id(
    disk_stable_id: str,
    partuuid: str,
) -> str:
    return f"disk:{disk_stable_id}:existing-partition:{partuuid}"
