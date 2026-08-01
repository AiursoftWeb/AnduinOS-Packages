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
