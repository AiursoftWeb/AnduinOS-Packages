"""Pure, command-free model for first-generation manual GPT layouts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .model import Filesystem
from .storage_inventory import DiskInventory, PartitionInventory


MIB = 1024 * 1024
MINIMUM_NEW_ESP_MIB = 512
MINIMUM_ROOT_MIB = 20 * 1024
MINIMUM_NTFS_MIB = 4 * 1024
MICROSOFT_LDM_METADATA_GUID = "5808c8aa-7e8f-42e0-85d2-e1e90434cfb3"
MICROSOFT_LDM_DATA_GUID = "af9b60a0-1431-4f62-bc68-3311714a69ad"
UNSUPPORTED_MANUAL_FILESYSTEMS = {
    "bitlocker",
    "crypto_luks",
    "linux_raid_member",
    "lvm2_member",
}


class ManualPartitionRole(str, Enum):
    EFI_SYSTEM = "efi-system"
    ROOT = "root"
    SWAP = "swap"


@dataclass(frozen=True)
class ManualPartitionRequest:
    """One new partition with an absolute, end-exclusive MiB range."""

    role: ManualPartitionRole
    start_mib: int
    end_mib: int

    @property
    def size_mib(self) -> int:
        return self.end_mib - self.start_mib


@dataclass(frozen=True)
class ManualPartitionResizeRequest:
    """Shrink one existing plain NTFS partition without moving its start."""

    partuuid: str
    original_size_bytes: int
    target_size_mib: int


@dataclass(frozen=True)
class ManualStorageSelection:
    """Unprivileged edits to one immutable disk-topology snapshot."""

    disk_stable_id: str
    disk_size_bytes: int
    disk_topology_digest: str
    reinitialize_gpt: bool
    deleted_partuuids: tuple[str, ...]
    reused_esp_partuuid: str
    filesystem: Filesystem
    new_partitions: tuple[ManualPartitionRequest, ...]
    resized_partitions: tuple[ManualPartitionResizeRequest, ...] = ()


@dataclass(frozen=True)
class ManualFreeExtent:
    """A MiB-aligned, end-exclusive range available to the editor."""

    start_mib: int
    end_mib: int

    @property
    def size_mib(self) -> int:
        return self.end_mib - self.start_mib


class ManualLayoutError(ValueError):
    pass


def validate_manual_selection(
    disk: DiskInventory,
    selection: ManualStorageSelection,
) -> None:
    """Validate every first-version manual-partitioning invariant."""

    if selection.disk_stable_id != disk.identity.stable_id:
        raise ManualLayoutError("Manual layout targets a different disk")
    if selection.disk_size_bytes != disk.identity.expected_size_bytes:
        raise ManualLayoutError("Selected disk size changed")
    if selection.disk_topology_digest != disk.topology_digest:
        raise ManualLayoutError("Selected disk topology changed")
    if not isinstance(selection.reinitialize_gpt, bool):
        raise ManualLayoutError("GPT reinitialization choice must be boolean")
    if not isinstance(selection.filesystem, Filesystem):
        raise ManualLayoutError("Manual root filesystem is invalid")

    reason = manual_layout_block_reason(
        disk,
        reinitialize_gpt=selection.reinitialize_gpt,
        allowed_active_swap_partuuids=(
            tuple(
                item.identity.partuuid
                for item in disk.partitions
                if item.filesystem_type.casefold() == "swap"
            )
            if selection.reinitialize_gpt
            else selection.deleted_partuuids
        ),
    )
    if reason:
        raise ManualLayoutError(reason)

    existing = {
        item.identity.partuuid: item
        for item in disk.partitions
        if item.identity.partuuid
    }
    deleted = selection.deleted_partuuids
    if len(deleted) != len(set(deleted)):
        raise ManualLayoutError("A partition is marked for deletion twice")
    if any(not item or item not in existing for item in deleted):
        raise ManualLayoutError("A deleted partition identity changed")
    if selection.reinitialize_gpt and deleted:
        raise ManualLayoutError(
            "A replacement GPT cannot also carry individual deletions"
        )

    resized = selection.resized_partitions
    if not isinstance(resized, tuple) or not all(
        isinstance(item, ManualPartitionResizeRequest) for item in resized
    ):
        raise ManualLayoutError("Partition resizes must be an ordered tuple")
    resized_ids = tuple(item.partuuid for item in resized)
    if len(resized_ids) != len(set(resized_ids)):
        raise ManualLayoutError("A partition is resized twice")
    expected_resize_order = tuple(
        item.identity.partuuid
        for item in disk.partitions
        if item.identity.partuuid in set(resized_ids)
    )
    if resized_ids != expected_resize_order:
        raise ManualLayoutError("Partition resizes must follow disk order")
    if selection.reinitialize_gpt and resized:
        raise ManualLayoutError("A replacement GPT cannot resize partitions")
    if set(resized_ids) & set(deleted):
        raise ManualLayoutError("A partition cannot be resized and deleted")
    if any(item.is_bitlocker_partition for item in disk.partitions) and resized:
        raise ManualLayoutError(
            "BitLocker must be fully disabled before shrinking this disk"
        )
    for request in resized:
        partition = existing.get(request.partuuid)
        if partition is None:
            raise ManualLayoutError("A resized partition identity changed")
        if partition.filesystem_type.casefold() != "ntfs":
            raise ManualLayoutError("Only plain NTFS partitions can be resized")
        if partition.mountpoints:
            raise ManualLayoutError("A resized NTFS partition is mounted")
        if request.original_size_bytes != partition.identity.size_bytes:
            raise ManualLayoutError("A resized partition size changed")
        current_size_mib = partition.identity.size_bytes // MIB
        if (
            type(request.target_size_mib) is not int
            or request.target_size_mib < MINIMUM_NTFS_MIB
            or request.target_size_mib >= current_size_mib
        ):
            raise ManualLayoutError("The requested NTFS size is invalid")

    reused_esp = selection.reused_esp_partuuid
    if selection.reinitialize_gpt and reused_esp:
        raise ManualLayoutError(
            "An existing EFI partition cannot survive GPT reinitialization"
        )
    if reused_esp:
        partition = existing.get(reused_esp)
        if partition is None or reused_esp in deleted:
            raise ManualLayoutError("Selected EFI System Partition changed")
        if not partition.is_efi_filesystem_candidate:
            raise ManualLayoutError(
                "Selected partition is not a reusable FAT EFI System Partition"
            )
        if not partition.filesystem_uuid:
            raise ManualLayoutError(
                "Selected EFI System Partition has no filesystem identity"
            )

    requests = selection.new_partitions
    if not isinstance(requests, tuple) or not all(
        isinstance(item, ManualPartitionRequest) for item in requests
    ):
        raise ManualLayoutError("Manual partitions must be an ordered tuple")
    ordered = tuple(
        sorted(requests, key=lambda item: (item.start_mib, item.end_mib))
    )
    if requests != ordered:
        raise ManualLayoutError("Manual partitions must be ordered by geometry")
    roles = tuple(item.role for item in requests)
    if any(not isinstance(role, ManualPartitionRole) for role in roles):
        raise ManualLayoutError("Manual partition role is invalid")
    if len(roles) != len(set(roles)):
        raise ManualLayoutError("Each manual partition role may appear once")
    if roles.count(ManualPartitionRole.ROOT) != 1:
        raise ManualLayoutError("Manual layout requires exactly one Root partition")
    new_esp_count = roles.count(ManualPartitionRole.EFI_SYSTEM)
    if new_esp_count + bool(reused_esp) != 1:
        raise ManualLayoutError(
            "Manual layout requires exactly one new or reused EFI partition"
        )
    if roles.count(ManualPartitionRole.SWAP) > 1:
        raise ManualLayoutError("Manual layout supports at most one Swap partition")

    allocatable = _base_allocatable_extents(disk, selection)
    previous_end = -1
    for request in requests:
        if (
            type(request.start_mib) is not int
            or type(request.end_mib) is not int
            or request.start_mib < 1
            or request.end_mib <= request.start_mib
        ):
            raise ManualLayoutError("Manual partition geometry is invalid")
        if request.start_mib < previous_end:
            raise ManualLayoutError("Manual partitions overlap")
        previous_end = request.end_mib
        if not any(
            request.start_mib >= extent.start_mib
            and request.end_mib <= extent.end_mib
            for extent in allocatable
        ):
            raise ManualLayoutError(
                f"{request.role.value} does not fit in available space"
            )
        if (
            request.role is ManualPartitionRole.EFI_SYSTEM
            and request.size_mib < MINIMUM_NEW_ESP_MIB
        ):
            raise ManualLayoutError(
                "A new EFI System Partition must be at least 512 MiB"
            )
        if (
            request.role is ManualPartitionRole.ROOT
            and request.size_mib < MINIMUM_ROOT_MIB
        ):
            raise ManualLayoutError(
                "The Root partition must be at least 20 GiB"
            )


def manual_available_extents(
    disk: DiskInventory,
    selection: ManualStorageSelection,
) -> tuple[ManualFreeExtent, ...]:
    """Return remaining aligned space after applying in-memory new partitions."""

    base = _base_allocatable_extents(disk, selection)
    occupied = tuple(
        ManualFreeExtent(item.start_mib, item.end_mib)
        for item in selection.new_partitions
    )
    return _subtract_extents(base, occupied)


def manual_layout_block_reason(
    disk: DiskInventory,
    *,
    reinitialize_gpt: bool,
    allowed_active_swap_partuuids: tuple[str, ...] = (),
) -> str:
    """Explain why this disk cannot enter the bounded manual editor."""

    if disk.geometry_probe_error:
        return "Complete partition geometry is unavailable"
    if not reinitialize_gpt and disk.partition_table != "gpt":
        return "Manual editing requires GPT or explicit GPT reinitialization"
    if disk.unsupported_descendant_types:
        return (
            "Manual editing does not support nested storage: "
            + ", ".join(disk.unsupported_descendant_types)
        )
    allowed_swaps = set(allowed_active_swap_partuuids)
    for partition in disk.partitions:
        active_deleted_swap = (
            partition.mountpoints == ("[SWAP]",)
            and partition.filesystem_type.casefold() == "swap"
            and partition.identity.partuuid in allowed_swaps
        )
        if partition.mountpoints and not active_deleted_swap:
            return f"Partition is mounted: {partition.identity.path}"
        filesystem = partition.filesystem_type.casefold()
        partition_type = partition.partition_type.strip("{}").casefold()
        if filesystem in UNSUPPORTED_MANUAL_FILESYSTEMS:
            return (
                "Manual editing does not support "
                f"{filesystem}: {partition.identity.path}"
            )
        if partition_type in {
            MICROSOFT_LDM_METADATA_GUID,
            MICROSOFT_LDM_DATA_GUID,
        }:
            return (
                "Manual editing does not support Windows Dynamic Disk: "
                f"{partition.identity.path}"
            )
        if not reinitialize_gpt and not partition.identity.partuuid:
            return (
                "An existing partition has no stable GPT identity: "
                f"{partition.identity.path}"
            )
    return ""


def partition_for_partuuid(
    disk: DiskInventory,
    partuuid: str,
) -> PartitionInventory:
    for partition in disk.partitions:
        if partition.identity.partuuid == partuuid:
            return partition
    raise ManualLayoutError("Partition identity changed")


def resize_for_partuuid(
    selection: ManualStorageSelection,
    partuuid: str,
) -> ManualPartitionResizeRequest | None:
    return next(
        (
            item
            for item in selection.resized_partitions
            if item.partuuid == partuuid
        ),
        None,
    )


def _base_allocatable_extents(
    disk: DiskInventory,
    selection: ManualStorageSelection,
) -> tuple[ManualFreeExtent, ...]:
    if selection.reinitialize_gpt:
        disk_end_mib = disk.identity.expected_size_bytes // MIB - 1
        return (
            (ManualFreeExtent(1, disk_end_mib),)
            if disk_end_mib > 1
            else ()
        )

    deleted = set(selection.deleted_partuuids)
    raw = [
        _aligned_extent(item.start_bytes, item.size_bytes)
        for item in disk.free_extents
    ]
    raw.extend(
        _aligned_extent(
            partition.identity.start_bytes,
            partition.identity.size_bytes,
        )
        for partition in disk.partitions
        if partition.identity.partuuid in deleted
    )
    for request in selection.resized_partitions:
        partition = partition_for_partuuid(disk, request.partuuid)
        target_size_bytes = request.target_size_mib * MIB
        raw.append(
            _aligned_extent(
                partition.identity.start_bytes + target_size_bytes,
                partition.identity.size_bytes - target_size_bytes,
            )
        )
    return _merge_extents(tuple(item for item in raw if item.size_mib > 0))


def _aligned_extent(start_bytes: int, size_bytes: int) -> ManualFreeExtent:
    start_mib = (start_bytes + MIB - 1) // MIB
    end_mib = (start_bytes + size_bytes) // MIB
    return ManualFreeExtent(start_mib, end_mib)


def _merge_extents(
    extents: tuple[ManualFreeExtent, ...],
) -> tuple[ManualFreeExtent, ...]:
    merged: list[ManualFreeExtent] = []
    for extent in sorted(extents, key=lambda item: item.start_mib):
        if not merged or extent.start_mib > merged[-1].end_mib:
            merged.append(extent)
            continue
        previous = merged[-1]
        merged[-1] = ManualFreeExtent(
            previous.start_mib,
            max(previous.end_mib, extent.end_mib),
        )
    return tuple(merged)


def _subtract_extents(
    available: tuple[ManualFreeExtent, ...],
    occupied: tuple[ManualFreeExtent, ...],
) -> tuple[ManualFreeExtent, ...]:
    result = list(available)
    for used in sorted(occupied, key=lambda item: item.start_mib):
        updated: list[ManualFreeExtent] = []
        for extent in result:
            if used.end_mib <= extent.start_mib or used.start_mib >= extent.end_mib:
                updated.append(extent)
                continue
            if used.start_mib > extent.start_mib:
                updated.append(
                    ManualFreeExtent(extent.start_mib, used.start_mib)
                )
            if used.end_mib < extent.end_mib:
                updated.append(
                    ManualFreeExtent(used.end_mib, extent.end_mib)
                )
        result = updated
    return tuple(item for item in result if item.size_mib > 0)
