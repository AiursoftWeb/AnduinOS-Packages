"""Runtime preservation proofs for guided coexistence execution."""

from __future__ import annotations

from dataclasses import dataclass

from .model import InstallMode, InstallPlan
from .storage_inventory import StorageInventory
from .storage_graph import GraphFilesystem
from .storage_write_set import StorageAction, StorageWriteSet


class PreservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreservedPartition:
    number: int
    partuuid: str
    start_bytes: int
    size_bytes: int
    partition_type: str
    filesystem_type: str
    filesystem_uuid: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class GuidedPreservationSnapshot:
    disk_stable_id: str
    disk_size_bytes: int
    partition_table: str
    partition_table_uuid: str
    partitions: tuple[PreservedPartition, ...]


@dataclass(frozen=True)
class ManualPreservationSnapshot:
    disk_stable_id: str
    disk_size_bytes: int
    partition_table: str
    partition_table_uuid: str
    reinitializes_gpt: bool
    partitions: tuple[PreservedPartition, ...]
    resized_partitions: tuple[tuple[PreservedPartition, int], ...]
    deleted_partuuids: tuple[str, ...]


def capture_guided_preservation_snapshot(
    plan: InstallPlan,
    inventory: StorageInventory,
    write_set: StorageWriteSet,
) -> GuidedPreservationSnapshot:
    """Freeze every preserve-marked partition before the first disk write."""

    if plan.storage.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise PreservationError("Preservation snapshots require guided mode")
    if write_set.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise PreservationError("Preservation write set has the wrong mode")
    try:
        disk = inventory.disk(plan.storage.disk.stable_id)
    except KeyError as error:
        raise PreservationError("Selected disk disappeared") from error
    if disk.identity.expected_size_bytes != plan.storage.disk.expected_size_bytes:
        raise PreservationError("Selected disk size changed")

    preserved_partuuids = tuple(
        item.detail("partuuid")
        for item in write_set.operations
        if item.action is StorageAction.PRESERVE
    )
    actual_partuuids = tuple(
        item.identity.partuuid for item in disk.partitions
    )
    if preserved_partuuids != actual_partuuids:
        raise PreservationError(
            "The guided write set does not preserve every existing partition"
        )

    return GuidedPreservationSnapshot(
        disk_stable_id=disk.identity.stable_id,
        disk_size_bytes=disk.identity.expected_size_bytes,
        partition_table=disk.partition_table,
        partition_table_uuid=disk.partition_table_uuid,
        partitions=tuple(
            PreservedPartition(
                number=item.identity.number,
                partuuid=item.identity.partuuid,
                start_bytes=item.identity.start_bytes,
                size_bytes=item.identity.size_bytes,
                partition_type=item.partition_type,
                filesystem_type=item.filesystem_type,
                filesystem_uuid=item.filesystem_uuid,
                flags=tuple(sorted(item.flags)),
            )
            for item in disk.partitions
        ),
    )


def verify_guided_preservation_snapshot(
    snapshot: GuidedPreservationSnapshot,
    inventory: StorageInventory,
) -> None:
    """Reject any identity, boundary, type or filesystem drift after writes."""

    try:
        disk = inventory.disk(snapshot.disk_stable_id)
    except KeyError as error:
        raise PreservationError(
            "Selected disk disappeared after partitioning"
        ) from error
    if disk.identity.expected_size_bytes != snapshot.disk_size_bytes:
        raise PreservationError("Selected disk size changed after partitioning")
    if (
        disk.partition_table != snapshot.partition_table
        or disk.partition_table_uuid != snapshot.partition_table_uuid
    ):
        raise PreservationError("Existing partition table identity changed")

    current = {item.identity.partuuid: item for item in disk.partitions}
    for expected in snapshot.partitions:
        actual = current.get(expected.partuuid)
        if actual is None:
            raise PreservationError(
                f"Preserved partition disappeared: {expected.partuuid}"
            )
        observed = PreservedPartition(
            number=actual.identity.number,
            partuuid=actual.identity.partuuid,
            start_bytes=actual.identity.start_bytes,
            size_bytes=actual.identity.size_bytes,
            partition_type=actual.partition_type,
            filesystem_type=actual.filesystem_type,
            filesystem_uuid=actual.filesystem_uuid,
            flags=tuple(sorted(actual.flags)),
        )
        if observed != expected:
            raise PreservationError(
                f"Preserved partition changed: {expected.partuuid}"
            )


def verify_guided_storage_result(
    plan: InstallPlan,
    snapshot: GuidedPreservationSnapshot,
    inventory: StorageInventory,
) -> None:
    """Verify preserved objects and every newly declared partition."""

    verify_guided_preservation_snapshot(snapshot, inventory)
    graph = plan.storage.graph
    if graph is None:
        raise PreservationError("Guided storage graph is missing")
    disk = inventory.disk(snapshot.disk_stable_id)
    current_by_number = {
        item.identity.number: item for item in disk.partitions
    }
    expected_numbers = {
        item.number for item in snapshot.partitions
    } | {item.number for item in graph.partitions}
    if set(current_by_number) != expected_numbers:
        raise PreservationError(
            "Partition result contains missing or undeclared partitions"
        )

    filesystem_by_block = {
        item.block_id: item.filesystem for item in graph.filesystems
    }
    accepted_types = {
        GraphFilesystem.VFAT: {"fat", "fat16", "fat32", "vfat"},
        GraphFilesystem.SWAP: {"swap"},
        GraphFilesystem.BTRFS: {"btrfs"},
        GraphFilesystem.EXT4: {"ext4"},
        GraphFilesystem.XFS: {"xfs"},
        GraphFilesystem.F2FS: {"f2fs"},
    }
    mib = 1024 * 1024
    for declaration in graph.partitions:
        actual = current_by_number.get(declaration.number)
        if actual is None or declaration.end_mib is None:
            raise PreservationError(
                f"New partition is missing: {declaration.name}"
            )
        if (
            actual.identity.start_bytes != declaration.start_mib * mib
            or actual.identity.size_bytes
            != (declaration.end_mib - declaration.start_mib) * mib
        ):
            raise PreservationError(
                f"New partition geometry changed: {declaration.name}"
            )
        filesystem = filesystem_by_block[declaration.partition_id]
        if actual.filesystem_type.casefold() not in accepted_types[filesystem]:
            raise PreservationError(
                f"New partition filesystem changed: {declaration.name}"
            )
        if not actual.identity.partuuid or not actual.filesystem_uuid:
            raise PreservationError(
                f"New partition identity is missing: {declaration.name}"
            )


def capture_manual_preservation_snapshot(
    plan: InstallPlan,
    inventory: StorageInventory,
    write_set: StorageWriteSet,
) -> ManualPreservationSnapshot:
    """Freeze exactly the objects a canonical manual plan will preserve."""

    if plan.storage.mode is not InstallMode.MANUAL:
        raise PreservationError("Manual snapshot requires manual mode")
    if write_set.mode is not InstallMode.MANUAL:
        raise PreservationError("Manual write set has the wrong mode")
    try:
        disk = inventory.disk(plan.storage.disk.stable_id)
    except KeyError as error:
        raise PreservationError("Selected disk disappeared") from error

    preserved_partuuids = tuple(
        item.detail("partuuid")
        for item in write_set.operations
        if item.action is StorageAction.PRESERVE
    )
    deleted_partuuids = tuple(
        item.detail("partuuid")
        for item in write_set.operations
        if item.action is StorageAction.DELETE_PARTITION
    )
    resized_targets = tuple(
        (
            item.detail("partuuid"),
            int(item.detail("target_size_bytes")),
        )
        for item in write_set.operations
        if item.action is StorageAction.RESIZE_PARTITION
    )
    resized_partuuids = tuple(item[0] for item in resized_targets)
    replaces_table = any(
        item.action is StorageAction.REPLACE_PARTITION_TABLE
        for item in write_set.operations
    )
    existing_partuuids = tuple(
        item.identity.partuuid for item in disk.partitions
    )
    if replaces_table:
        if preserved_partuuids or deleted_partuuids or resized_partuuids:
            raise PreservationError(
                "A replacement GPT cannot preserve individual partitions"
            )
        deleted_partuuids = existing_partuuids
    elif (
        set(preserved_partuuids)
        | set(deleted_partuuids)
        | set(resized_partuuids)
    ) != set(
        existing_partuuids
    ):
        raise PreservationError(
            "Every existing manual partition must be preserved, deleted or resized"
        )

    preserved = set(preserved_partuuids)
    return ManualPreservationSnapshot(
        disk_stable_id=disk.identity.stable_id,
        disk_size_bytes=disk.identity.expected_size_bytes,
        partition_table=disk.partition_table,
        partition_table_uuid=disk.partition_table_uuid,
        reinitializes_gpt=replaces_table,
        partitions=tuple(
            _preserved_partition(item)
            for item in disk.partitions
            if item.identity.partuuid in preserved
        ),
        resized_partitions=tuple(
            (
                _preserved_partition(
                    next(
                        item
                        for item in disk.partitions
                        if item.identity.partuuid == partuuid
                    )
                ),
                target_size_bytes,
            )
            for partuuid, target_size_bytes in resized_targets
        ),
        deleted_partuuids=deleted_partuuids,
    )


def verify_manual_storage_result(
    plan: InstallPlan,
    snapshot: ManualPreservationSnapshot,
    inventory: StorageInventory,
) -> None:
    """Prove that only declared manual deletes and creates occurred."""

    try:
        disk = inventory.disk(snapshot.disk_stable_id)
    except KeyError as error:
        raise PreservationError(
            "Selected disk disappeared after partitioning"
        ) from error
    if disk.identity.expected_size_bytes != snapshot.disk_size_bytes:
        raise PreservationError("Selected disk size changed after partitioning")
    if disk.partition_table != "gpt":
        raise PreservationError("Manual installation did not produce GPT")
    if not snapshot.reinitializes_gpt and (
        disk.partition_table != snapshot.partition_table
        or disk.partition_table_uuid != snapshot.partition_table_uuid
    ):
        raise PreservationError("Preserved partition table identity changed")

    current_by_partuuid = {
        item.identity.partuuid: item for item in disk.partitions
    }
    for expected in snapshot.partitions:
        actual = current_by_partuuid.get(expected.partuuid)
        if actual is None:
            raise PreservationError(
                f"Preserved partition disappeared: {expected.partuuid}"
            )
        if _preserved_partition(actual) != expected:
            raise PreservationError(
                f"Preserved partition changed: {expected.partuuid}"
            )
    for before, target_size_bytes in snapshot.resized_partitions:
        actual = current_by_partuuid.get(before.partuuid)
        if actual is None:
            raise PreservationError(
                f"Resized partition disappeared: {before.partuuid}"
            )
        after = _preserved_partition(actual)
        if (
            after.number != before.number
            or after.partuuid != before.partuuid
            or after.start_bytes != before.start_bytes
            or after.size_bytes != target_size_bytes
            or after.partition_type != before.partition_type
            or after.filesystem_type.casefold() != "ntfs"
            or after.filesystem_uuid != before.filesystem_uuid
            or after.flags != before.flags
        ):
            raise PreservationError(
                f"Resized partition changed unexpectedly: {before.partuuid}"
            )
    for partuuid in snapshot.deleted_partuuids:
        if partuuid in current_by_partuuid:
            raise PreservationError(
                f"Deleted partition still exists: {partuuid}"
            )

    graph = plan.storage.graph
    if graph is None:
        raise PreservationError("Manual storage graph is missing")
    current_by_number = {
        item.identity.number: item for item in disk.partitions
    }
    expected_numbers = {
        item.number for item in snapshot.partitions
    } | {
        item.number for item, _target in snapshot.resized_partitions
    } | {item.number for item in graph.partitions}
    if set(current_by_number) != expected_numbers:
        raise PreservationError(
            "Manual result contains missing or undeclared partitions"
        )
    _verify_new_partitions(graph, current_by_number)


def _preserved_partition(partition) -> PreservedPartition:
    return PreservedPartition(
        number=partition.identity.number,
        partuuid=partition.identity.partuuid,
        start_bytes=partition.identity.start_bytes,
        size_bytes=partition.identity.size_bytes,
        partition_type=partition.partition_type,
        filesystem_type=partition.filesystem_type,
        filesystem_uuid=partition.filesystem_uuid,
        flags=tuple(sorted(partition.flags)),
    )


def _verify_new_partitions(graph, current_by_number) -> None:
    filesystem_by_block = {
        item.block_id: item.filesystem for item in graph.filesystems
    }
    accepted_types = {
        GraphFilesystem.VFAT: {"fat", "fat16", "fat32", "vfat"},
        GraphFilesystem.SWAP: {"swap"},
        GraphFilesystem.BTRFS: {"btrfs"},
        GraphFilesystem.EXT4: {"ext4"},
        GraphFilesystem.XFS: {"xfs"},
        GraphFilesystem.F2FS: {"f2fs"},
    }
    mib = 1024 * 1024
    for declaration in graph.partitions:
        actual = current_by_number.get(declaration.number)
        if actual is None or declaration.end_mib is None:
            raise PreservationError(
                f"New partition is missing: {declaration.name}"
            )
        if (
            actual.identity.start_bytes != declaration.start_mib * mib
            or actual.identity.size_bytes
            != (declaration.end_mib - declaration.start_mib) * mib
        ):
            raise PreservationError(
                f"New partition geometry changed: {declaration.name}"
            )
        filesystem = filesystem_by_block[declaration.partition_id]
        if actual.filesystem_type.casefold() not in accepted_types[filesystem]:
            raise PreservationError(
                f"New partition filesystem changed: {declaration.name}"
            )
        if not actual.identity.partuuid or not actual.filesystem_uuid:
            raise PreservationError(
                f"New partition identity is missing: {declaration.name}"
            )
