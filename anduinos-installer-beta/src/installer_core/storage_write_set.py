"""Typed storage write-set planning for user-visible confirmation.

The first implementation describes the existing erase-disk path only. It is
pure and does not replace or execute the already-tested command plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .btrfs import BTRFS_SUBVOLUMES
from .boot_commands import guided_loader_path
from .layout import PartitionLayout, PartitionSpec, build_erase_disk_layout
from .model import (
    Architecture,
    Filesystem,
    InstallMode,
    InstallPlan,
)
from .storage_graph import (
    BlockReferenceKind,
    GraphFilesystem,
    StorageGraphAction,
)
from .storage_commands import partition_path
from .storage_inventory import DiskInventory, StorageInventory
from .validation import validate_plan


class StorageObjectKind(str, Enum):
    DISK = "disk"
    PARTITION = "partition"
    FILESYSTEM = "filesystem"
    SUBVOLUME = "subvolume"
    EFI_SYSTEM_PARTITION = "efi-system-partition"


class StorageAction(str, Enum):
    PRESERVE = "preserve"
    REPLACE_PARTITION_TABLE = "replace-partition-table"
    MODIFY_PARTITION_TABLE = "modify-partition-table"
    CREATE_PARTITION = "create-partition"
    FORMAT = "format"
    CREATE_SUBVOLUME = "create-subvolume"
    CONFIGURE_MOUNTS = "configure-mounts"
    WRITE_BIOS_BOOTLOADER = "write-bios-bootloader"
    WRITE_BOOT_FILES = "write-boot-files"
    WRITE_FALLBACK_BOOT_FILES = "write-fallback-boot-files"
    UPDATE_NVRAM = "update-nvram"


@dataclass(frozen=True)
class StorageWriteOperation:
    action: StorageAction
    target_kind: StorageObjectKind
    target_id: str
    display_path: str
    destructive: bool
    details: tuple[tuple[str, str], ...] = ()

    def detail(self, name: str) -> str:
        for key, value in self.details:
            if key == name:
                return value
        raise KeyError(name)


@dataclass(frozen=True)
class StorageWriteSet:
    mode: InstallMode
    disk_stable_id: str
    operations: tuple[StorageWriteOperation, ...]

    @property
    def destructive_operations(self) -> tuple[StorageWriteOperation, ...]:
        return tuple(item for item in self.operations if item.destructive)


def build_erase_disk_write_set(plan: InstallPlan) -> StorageWriteSet:
    """Describe every storage write in the current whole-disk layout."""

    validate_plan(plan)
    if plan.storage.mode is not InstallMode.ERASE_DISK:
        raise ValueError("Only erase-disk write sets are implemented")
    layout = build_erase_disk_layout(plan)
    disk = plan.storage.disk
    disk_id = f"disk:{disk.stable_id}"
    operations: list[StorageWriteOperation] = [
        StorageWriteOperation(
            action=StorageAction.REPLACE_PARTITION_TABLE,
            target_kind=StorageObjectKind.DISK,
            target_id=disk_id,
            display_path=disk.path,
            destructive=True,
            details=(("table", layout.table),),
        )
    ]

    partition_ids: dict[str, str] = {}
    partition_paths: dict[str, str] = {}
    for partition in layout.partitions:
        target_id = f"{disk_id}:partition:{partition.number}"
        display_path = partition_path(disk.path, partition.number)
        partition_ids[partition.name] = target_id
        partition_paths[partition.name] = display_path
        operations.append(
            StorageWriteOperation(
                action=StorageAction.CREATE_PARTITION,
                target_kind=(
                    StorageObjectKind.EFI_SYSTEM_PARTITION
                    if partition.name == "efi-system"
                    else StorageObjectKind.PARTITION
                ),
                target_id=target_id,
                display_path=display_path,
                destructive=False,
                details=_partition_details(partition, layout),
            )
        )

    format_types = {
        "efi-system": "vfat",
        "root": plan.storage.filesystem.value,
    }
    if "swap" in partition_ids:
        format_types["swap"] = "swap"
    format_names = (
        "efi-system",
        *(("swap",) if "swap" in partition_ids else ()),
        "root",
    )
    for name in format_names:
        operations.append(
            StorageWriteOperation(
                action=StorageAction.FORMAT,
                target_kind=(
                    StorageObjectKind.EFI_SYSTEM_PARTITION
                    if name == "efi-system"
                    else StorageObjectKind.FILESYSTEM
                ),
                target_id=partition_ids[name],
                display_path=partition_paths[name],
                destructive=True,
                details=(("filesystem", format_types[name]),),
            )
        )

    if plan.storage.filesystem is Filesystem.BTRFS:
        root_id = partition_ids["root"]
        root_path = partition_paths["root"]
        for subvolume in BTRFS_SUBVOLUMES:
            operations.append(
                StorageWriteOperation(
                    action=StorageAction.CREATE_SUBVOLUME,
                    target_kind=StorageObjectKind.SUBVOLUME,
                    target_id=f"{root_id}:subvolume:{subvolume.name}",
                    display_path=f"{root_path}[{subvolume.name}]",
                    destructive=False,
                    details=(
                        ("name", subvolume.name),
                        ("mount_point", subvolume.mount_point),
                        (
                            "rollback_with_system",
                            str(subvolume.rollback_with_system).lower(),
                        ),
                    ),
                )
            )

    root_id = partition_ids["root"]
    root_path = partition_paths["root"]
    operations.append(
        StorageWriteOperation(
            action=StorageAction.CONFIGURE_MOUNTS,
            target_kind=StorageObjectKind.FILESYSTEM,
            target_id=root_id,
            display_path=root_path,
            destructive=False,
            details=(
                ("fstab", "/etc/fstab"),
                ("zram", "/etc/systemd/zram-generator.conf"),
            ),
        )
    )

    if plan.platform.architecture is Architecture.AMD64:
        operations.append(
            StorageWriteOperation(
                action=StorageAction.WRITE_BIOS_BOOTLOADER,
                target_kind=StorageObjectKind.DISK,
                target_id=disk_id,
                display_path=disk.path,
                destructive=False,
                details=(("target", "i386-pc"),),
            )
        )

    esp_id = partition_ids["efi-system"]
    esp_path = partition_paths["efi-system"]
    operations.append(
        StorageWriteOperation(
            action=StorageAction.WRITE_BOOT_FILES,
            target_kind=StorageObjectKind.EFI_SYSTEM_PARTITION,
            target_id=esp_id,
            display_path=esp_path,
            destructive=False,
            details=(("directory", "EFI/AnduinOS"),),
        )
    )
    fallback = (
        "EFI/BOOT/BOOTX64.EFI"
        if plan.platform.architecture is Architecture.AMD64
        else "EFI/BOOT/BOOTAA64.EFI"
    )
    operations.append(
        StorageWriteOperation(
            action=StorageAction.WRITE_FALLBACK_BOOT_FILES,
            target_kind=StorageObjectKind.EFI_SYSTEM_PARTITION,
            target_id=esp_id,
            display_path=esp_path,
            destructive=False,
            details=(("path", fallback),),
        )
    )
    return StorageWriteSet(
        mode=plan.storage.mode,
        disk_stable_id=disk.stable_id,
        operations=tuple(operations),
    )


def build_guided_coexistence_write_set(
    plan: InstallPlan,
    inventory: StorageInventory,
) -> StorageWriteSet:
    """Build the confirmation set from a freshly reconstructed graph."""

    if plan.storage.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise ValueError("Plan is not guided coexistence")
    from .storage_graph_planning import validate_guided_coexistence_graph

    disk = validate_guided_coexistence_graph(plan, inventory)
    return _build_guided_write_set_from_disk(plan, disk)


def _build_guided_write_set_from_disk(
    plan: InstallPlan,
    disk: DiskInventory,
) -> StorageWriteSet:
    graph = plan.storage.graph
    assert graph is not None
    references = {item.reference_id: item for item in graph.block_references}
    partitions = {item.partition_id: item for item in graph.partitions}
    filesystems = {item.block_id: item for item in graph.filesystems}
    subvolumes = {item.subvolume_id: item for item in graph.subvolumes}
    existing = {
        _existing_partition_reference_id(
            disk.identity.stable_id, item.identity.partuuid
        ): item
        for item in disk.partitions
    }
    new_paths = {
        item.partition_id: partition_path(disk.identity.path, item.number)
        for item in graph.partitions
    }
    boot_target = graph.boot_targets[0]
    operations: list[StorageWriteOperation] = []

    for operation in graph.operations:
        action = StorageAction(operation.action.value)
        target_id = operation.target_id
        if operation.action is StorageGraphAction.PRESERVE:
            partition = existing[target_id]
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=(
                        StorageObjectKind.EFI_SYSTEM_PARTITION
                        if partition.is_efi_system_partition
                        else StorageObjectKind.PARTITION
                    ),
                    target_id=target_id,
                    display_path=partition.identity.path,
                    destructive=False,
                    details=(
                        ("partuuid", partition.identity.partuuid),
                        ("start_bytes", str(partition.identity.start_bytes)),
                        ("size_bytes", str(partition.identity.size_bytes)),
                    ),
                )
            )
            continue
        if operation.action is StorageGraphAction.MODIFY_PARTITION_TABLE:
            extent = next(
                item
                for item in references.values()
                if item.kind is BlockReferenceKind.FREE_EXTENT
            )
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=StorageObjectKind.DISK,
                    target_id=target_id,
                    display_path=disk.identity.path,
                    destructive=True,
                    details=(
                        ("table", graph.partition_table),
                        ("free_extent_id", extent.stable_id),
                        ("start_bytes", str(extent.start_bytes)),
                        ("size_bytes", str(extent.expected_size_bytes)),
                    ),
                )
            )
            continue
        if operation.action is StorageGraphAction.CREATE_PARTITION:
            partition = partitions[target_id]
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=(
                        StorageObjectKind.EFI_SYSTEM_PARTITION
                        if partition.name == "efi-system"
                        else StorageObjectKind.PARTITION
                    ),
                    target_id=target_id,
                    display_path=new_paths[target_id],
                    destructive=False,
                    details=_guided_partition_details(partition),
                )
            )
            continue
        if operation.action is StorageGraphAction.FORMAT:
            filesystem = filesystems[target_id]
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=(
                        StorageObjectKind.EFI_SYSTEM_PARTITION
                        if filesystem.filesystem is GraphFilesystem.VFAT
                        else StorageObjectKind.FILESYSTEM
                    ),
                    target_id=target_id,
                    display_path=new_paths[target_id],
                    destructive=True,
                    details=(("filesystem", filesystem.filesystem.value),),
                )
            )
            continue
        if operation.action is StorageGraphAction.CREATE_SUBVOLUME:
            subvolume = subvolumes[target_id]
            root_path = new_paths[subvolume.filesystem_id]
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=StorageObjectKind.SUBVOLUME,
                    target_id=target_id,
                    display_path=f"{root_path}[{subvolume.name}]",
                    destructive=False,
                    details=(
                        ("name", subvolume.name),
                        ("mount_point", subvolume.mount_point),
                        (
                            "rollback_with_system",
                            str(subvolume.rollback_with_system).lower(),
                        ),
                    ),
                )
            )
            continue
        if operation.action is StorageGraphAction.CONFIGURE_MOUNTS:
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=StorageObjectKind.FILESYSTEM,
                    target_id=target_id,
                    display_path=new_paths[target_id],
                    destructive=False,
                    details=(
                        ("fstab", "/etc/fstab"),
                        ("zram", "/etc/systemd/zram-generator.conf"),
                    ),
                )
            )
            continue
        esp_path = _esp_display_path(boot_target.efi_filesystem_id, existing, new_paths)
        if operation.action is StorageGraphAction.WRITE_BOOT_FILES:
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=StorageObjectKind.EFI_SYSTEM_PARTITION,
                    target_id=target_id,
                    display_path=esp_path,
                    destructive=False,
                    details=(
                        ("directory", boot_target.vendor_directory),
                        (
                            "shared",
                            str(target_id in existing).lower(),
                        ),
                    ),
                )
            )
            continue
        if operation.action is StorageGraphAction.UPDATE_NVRAM:
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=StorageObjectKind.EFI_SYSTEM_PARTITION,
                    target_id=target_id,
                    display_path=esp_path,
                    destructive=False,
                    details=(
                        ("label", "AnduinOS"),
                        ("loader", guided_loader_path(plan)),
                    ),
                )
            )
            continue
        raise RuntimeError(f"Unsupported coexistence action: {operation.action}")

    return StorageWriteSet(
        mode=plan.storage.mode,
        disk_stable_id=disk.identity.stable_id,
        operations=tuple(operations),
    )


def _partition_details(
    partition: PartitionSpec, layout: PartitionLayout
) -> tuple[tuple[str, str], ...]:
    end = str(partition.end_mib) if partition.end_mib is not None else "end"
    return (
        ("name", partition.name),
        ("number", str(partition.number)),
        ("start_mib", str(partition.start_mib)),
        ("end_mib", end),
        ("filesystem_hint", partition.filesystem or ""),
        ("flags", ",".join(partition.flags)),
        ("partition_table", layout.table),
    )


def _guided_partition_details(partition) -> tuple[tuple[str, str], ...]:
    if partition.end_mib is None:
        raise RuntimeError("Coexistence partition has no bounded end")
    return (
        ("name", partition.name),
        ("number", str(partition.number)),
        ("start_mib", str(partition.start_mib)),
        ("end_mib", str(partition.end_mib)),
        ("flags", ",".join(partition.flags)),
        ("parent_free_extent", partition.parent_reference_id),
    )


def _esp_display_path(esp_id, existing, new_paths) -> str:
    if esp_id in existing:
        return existing[esp_id].identity.path
    return new_paths[esp_id]


def _existing_partition_reference_id(
    disk_stable_id: str,
    partuuid: str,
) -> str:
    return f"disk:{disk_stable_id}:existing-partition:{partuuid}"
