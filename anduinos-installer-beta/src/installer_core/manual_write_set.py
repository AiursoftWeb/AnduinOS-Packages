"""Typed user-visible write set for canonical manual GPT plans."""

from __future__ import annotations

from .boot_commands import guided_loader_path
from .manual_graph_planning import validate_manual_storage_graph
from .model import InstallMode, InstallPlan
from .storage_commands import partition_path
from .storage_graph import GraphFilesystem, StorageGraphAction
from .storage_inventory import DiskInventory, StorageInventory
from .storage_write_set import (
    StorageAction,
    StorageObjectKind,
    StorageWriteOperation,
    StorageWriteSet,
)


def build_manual_storage_write_set(
    plan: InstallPlan,
    inventory: StorageInventory,
) -> StorageWriteSet:
    """Describe every allowed manual write after a fresh topology check."""

    if plan.storage.mode is not InstallMode.MANUAL:
        raise ValueError("Plan is not manual partitioning")
    disk = validate_manual_storage_graph(plan, inventory)
    return _build_manual_write_set_from_disk(plan, disk)


def _build_manual_write_set_from_disk(
    plan: InstallPlan,
    disk: DiskInventory,
) -> StorageWriteSet:
    graph = plan.storage.graph
    assert graph is not None
    partitions = {item.partition_id: item for item in graph.partitions}
    filesystems = {item.block_id: item for item in graph.filesystems}
    subvolumes = {item.subvolume_id: item for item in graph.subvolumes}
    existing = {
        _existing_partition_reference_id(
            disk.identity.stable_id,
            item.identity.partuuid,
        ): item
        for item in disk.partitions
    }
    new_paths = {
        item.partition_id: partition_path(disk.identity.path, item.number)
        for item in graph.partitions
    }
    resizes = {
        item.target_reference_id: item for item in graph.partition_resizes
    }
    boot_target = graph.boot_targets[0]
    operations: list[StorageWriteOperation] = []

    for operation in graph.operations:
        action = StorageAction(operation.action.value)
        target_id = operation.target_id
        if operation.action in {
            StorageGraphAction.PRESERVE,
            StorageGraphAction.DELETE_PARTITION,
            StorageGraphAction.RESIZE_PARTITION,
        }:
            partition = existing[target_id]
            resize = resizes.get(target_id)
            details = [
                ("partuuid", partition.identity.partuuid),
                ("number", str(partition.identity.number)),
                ("start_bytes", str(partition.identity.start_bytes)),
                ("size_bytes", str(partition.identity.size_bytes)),
                ("filesystem", partition.filesystem_type),
            ]
            if resize is not None:
                details.extend(
                    (
                        ("original_size_bytes", str(resize.original_size_bytes)),
                        ("target_size_bytes", str(resize.target_size_bytes)),
                        (
                            "reclaimed_bytes",
                            str(
                                resize.original_size_bytes
                                - resize.target_size_bytes
                            ),
                        ),
                    )
                )
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
                    destructive=(
                        operation.action
                        in {
                            StorageGraphAction.DELETE_PARTITION,
                            StorageGraphAction.RESIZE_PARTITION,
                        }
                    ),
                    details=tuple(details),
                )
            )
            continue
        if operation.action in {
            StorageGraphAction.REPLACE_PARTITION_TABLE,
            StorageGraphAction.MODIFY_PARTITION_TABLE,
        }:
            operations.append(
                StorageWriteOperation(
                    action=action,
                    target_kind=StorageObjectKind.DISK,
                    target_id=target_id,
                    display_path=disk.identity.path,
                    destructive=True,
                    details=(("table", "gpt"),),
                )
            )
            continue
        if operation.action is StorageGraphAction.CREATE_PARTITION:
            partition = partitions[target_id]
            if partition.end_mib is None:
                raise RuntimeError("Manual partition has no bounded end")
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
                    details=(
                        ("name", partition.name),
                        ("number", str(partition.number)),
                        ("start_mib", str(partition.start_mib)),
                        ("end_mib", str(partition.end_mib)),
                        ("flags", ",".join(partition.flags)),
                    ),
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
                        ("zram", "/etc/default/anduinos-zram"),
                    ),
                )
            )
            continue

        esp_path = (
            existing[boot_target.efi_filesystem_id].identity.path
            if boot_target.efi_filesystem_id in existing
            else new_paths[boot_target.efi_filesystem_id]
        )
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
                        ("shared", str(target_id in existing).lower()),
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
        raise RuntimeError(f"Unsupported manual action: {operation.action}")

    return StorageWriteSet(
        mode=InstallMode.MANUAL,
        disk_stable_id=disk.identity.stable_id,
        operations=tuple(operations),
    )


def _existing_partition_reference_id(
    disk_stable_id: str,
    partuuid: str,
) -> str:
    return f"disk:{disk_stable_id}:existing-partition:{partuuid}"
