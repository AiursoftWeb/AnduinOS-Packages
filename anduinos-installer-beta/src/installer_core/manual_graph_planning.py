"""Build and revalidate canonical manual GPT storage graphs."""

from __future__ import annotations

import re

from .btrfs import BTRFS_SUBVOLUMES
from .manual_layout import (
    ManualPartitionRequest,
    ManualPartitionResizeRequest,
    ManualPartitionRole,
    ManualStorageSelection,
    validate_manual_selection,
)
from .model import Filesystem, Firmware, InstallMode, InstallPlan
from .storage_graph import (
    STORAGE_GRAPH_SCHEMA_VERSION,
    BlockReference,
    BlockReferenceKind,
    BootTarget,
    FilesystemDeclaration,
    GraphFilesystem,
    MountDeclaration,
    MountRole,
    PartitionDeclaration,
    PartitionResizeDeclaration,
    StorageCapability,
    StorageGraph,
    StorageGraphAction,
    StorageGraphMode,
    StorageGraphOperation,
    SubvolumeDeclaration,
)
from .storage_inventory import (
    DiskInventory,
    DiskTopologyBinding,
    PartitionInventory,
    StaleStorageInventoryError,
    StorageInventory,
    verify_disk_topology,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManualStorageGraphError(ValueError):
    pass


def build_manual_storage_graph(
    plan: InstallPlan,
    disk: DiskInventory,
    selection: ManualStorageSelection,
    *,
    inventory_digest: str,
) -> StorageGraph:
    """Build a command-free graph from one fully validated manual layout."""

    if plan.storage.mode is not InstallMode.MANUAL:
        raise ValueError("Plan is not manual partitioning")
    if plan.platform.firmware is not Firmware.UEFI:
        raise ValueError("Manual partitioning currently requires UEFI")
    if (
        plan.storage.disk.stable_id != disk.identity.stable_id
        or plan.storage.disk.expected_size_bytes
        != disk.identity.expected_size_bytes
    ):
        raise ValueError("Inventory disk does not match the selected disk")
    if plan.storage.filesystem is not selection.filesystem:
        raise ValueError("Manual root filesystem does not match the plan")
    validate_manual_selection(disk, selection)

    swap_size_mib = next(
        (
            item.size_mib
            for item in selection.new_partitions
            if item.role is ManualPartitionRole.SWAP
        ),
        0,
    )
    if plan.storage.swap_size_mib != swap_size_mib:
        raise ValueError("Manual Swap size does not match the partition graph")

    disk_id = _disk_id(disk.identity.stable_id)
    disk_reference = BlockReference(
        reference_id=disk_id,
        kind=BlockReferenceKind.DISK,
        stable_id=disk.identity.stable_id,
        parent_reference_id="",
        expected_size_bytes=disk.identity.expected_size_bytes,
        start_bytes=0,
        topology_digest=disk.topology_digest,
    )
    existing_references = (
        ()
        if selection.reinitialize_gpt
        else tuple(
            _partition_reference(disk_id, disk.topology_digest, item)
            for item in disk.partitions
        )
    )
    references_by_partuuid = {
        item.stable_id: item for item in existing_references
    }
    deleted = set(selection.deleted_partuuids)
    resized_requests = {
        item.partuuid: item for item in selection.resized_partitions
    }
    preserved_references = tuple(
        item
        for item in existing_references
        if item.stable_id not in deleted
        and item.stable_id not in resized_requests
    )
    deleted_references = tuple(
        item
        for item in existing_references
        if item.stable_id in deleted
    )
    resized_references = tuple(
        item
        for item in existing_references
        if item.stable_id in resized_requests
    )
    partition_resizes = tuple(
        PartitionResizeDeclaration(
            target_reference_id=item.reference_id,
            filesystem=GraphFilesystem.NTFS,
            original_size_bytes=item.expected_size_bytes,
            target_size_bytes=(
                resized_requests[item.stable_id].target_size_mib * 1024 * 1024
            ),
        )
        for item in resized_references
    )

    used_numbers = {
        item.identity.number
        for item in disk.partitions
        if not selection.reinitialize_gpt
        and item.identity.partuuid not in deleted
    }
    numbers = _next_partition_numbers(
        used_numbers,
        len(selection.new_partitions),
    )
    partitions: list[PartitionDeclaration] = []
    partition_ids: dict[ManualPartitionRole, str] = {}
    for request, number in zip(
        selection.new_partitions,
        numbers,
        strict=True,
    ):
        partition_id = f"{disk_id}:new-partition:{number}"
        partition_ids[request.role] = partition_id
        partitions.append(
            PartitionDeclaration(
                partition_id=partition_id,
                parent_reference_id=disk_id,
                number=number,
                name=request.role.value,
                start_mib=request.start_mib,
                end_mib=request.end_mib,
                flags=(
                    ("esp",)
                    if request.role is ManualPartitionRole.EFI_SYSTEM
                    else (
                        ("swap",)
                        if request.role is ManualPartitionRole.SWAP
                        else ()
                    )
                ),
            )
        )

    reused_esp = selection.reused_esp_partuuid
    esp_id = (
        references_by_partuuid[reused_esp].reference_id
        if reused_esp
        else partition_ids[ManualPartitionRole.EFI_SYSTEM]
    )
    root_id = partition_ids[ManualPartitionRole.ROOT]
    filesystems: list[FilesystemDeclaration] = [
        FilesystemDeclaration(
            filesystem_id=esp_id,
            block_id=esp_id,
            filesystem=GraphFilesystem.VFAT,
            label="" if reused_esp else "ANDUIN_EFI",
        )
    ]
    swap_id = partition_ids.get(ManualPartitionRole.SWAP)
    if swap_id:
        filesystems.append(
            FilesystemDeclaration(
                filesystem_id=swap_id,
                block_id=swap_id,
                filesystem=GraphFilesystem.SWAP,
                label="AnduinOS-swap",
            )
        )
    filesystems.append(
        FilesystemDeclaration(
            filesystem_id=root_id,
            block_id=root_id,
            filesystem=GraphFilesystem(plan.storage.filesystem.value),
            label="AnduinOS",
        )
    )

    capabilities = [StorageCapability.BOOTABLE]
    subvolumes: tuple[SubvolumeDeclaration, ...] = ()
    if plan.storage.filesystem is Filesystem.BTRFS:
        subvolumes = tuple(
            SubvolumeDeclaration(
                subvolume_id=f"{root_id}:subvolume:{item.name}",
                filesystem_id=root_id,
                name=item.name,
                mount_point=item.mount_point,
                rollback_with_system=item.rollback_with_system,
            )
            for item in BTRFS_SUBVOLUMES
        )
        mounts = tuple(
            MountDeclaration(
                source_id=item.subvolume_id,
                target_path=item.mount_point,
                role=_mount_role(item.mount_point),
            )
            for item in subvolumes
        )
        capabilities.extend(
            (
                StorageCapability.SYSTEM_ROLLBACK,
                StorageCapability.SNAPSHOT_MANAGEMENT,
            )
        )
    else:
        mounts = (
            MountDeclaration(
                source_id=root_id,
                target_path="/",
                role=MountRole.ROOT,
            ),
        )
    mounts += (
        MountDeclaration(
            source_id=esp_id,
            target_path="/boot/efi",
            role=MountRole.EFI,
        ),
    )

    operations: list[StorageGraphOperation] = []
    if selection.reinitialize_gpt:
        operations.append(
            StorageGraphOperation(
                StorageGraphAction.REPLACE_PARTITION_TABLE,
                disk_id,
            )
        )
    else:
        operations.extend(
            StorageGraphOperation(
                StorageGraphAction.PRESERVE,
                item.reference_id,
            )
            for item in preserved_references
        )
        operations.extend(
            StorageGraphOperation(
                StorageGraphAction.DELETE_PARTITION,
                item.reference_id,
            )
            for item in deleted_references
        )
        operations.extend(
            StorageGraphOperation(
                StorageGraphAction.RESIZE_PARTITION,
                item.target_reference_id,
            )
            for item in partition_resizes
        )
        operations.append(
            StorageGraphOperation(
                StorageGraphAction.MODIFY_PARTITION_TABLE,
                disk_id,
            )
        )
    operations.extend(
        StorageGraphOperation(
            StorageGraphAction.CREATE_PARTITION,
            item.partition_id,
        )
        for item in partitions
    )
    operations.extend(
        StorageGraphOperation(
            StorageGraphAction.FORMAT,
            item.partition_id,
        )
        for item in partitions
    )
    operations.extend(
        StorageGraphOperation(
            StorageGraphAction.CREATE_SUBVOLUME,
            item.subvolume_id,
        )
        for item in subvolumes
    )
    operations.extend(
        (
            StorageGraphOperation(
                StorageGraphAction.CONFIGURE_MOUNTS,
                root_id,
            ),
            StorageGraphOperation(StorageGraphAction.WRITE_BOOT_FILES, esp_id),
            StorageGraphOperation(StorageGraphAction.UPDATE_NVRAM, esp_id),
        )
    )
    return StorageGraph(
        schema_version=STORAGE_GRAPH_SCHEMA_VERSION,
        mode=StorageGraphMode.MANUAL,
        inventory_digest=inventory_digest,
        partition_table="gpt",
        block_references=(disk_reference, *existing_references),
        partitions=tuple(partitions),
        partition_resizes=partition_resizes,
        filesystems=tuple(filesystems),
        subvolumes=subvolumes,
        mounts=mounts,
        boot_targets=(
            BootTarget(
                efi_filesystem_id=esp_id,
                bios_disk_reference_id="",
                vendor_directory="EFI/AnduinOS",
                fallback_path="",
            ),
        ),
        operations=tuple(operations),
        requested_capabilities=tuple(capabilities),
    )


def validate_manual_graph_structure(
    plan: InstallPlan,
    graph: StorageGraph,
) -> None:
    """Reject unsupported manual-graph shapes before inventory resolution."""

    disk_references = tuple(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.DISK
    )
    partition_references = tuple(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.PARTITION
    )
    if len(disk_references) != 1 or len(graph.block_references) != (
        1 + len(partition_references)
    ):
        raise ManualStorageGraphError(
            "Manual graph supports one disk and direct GPT partitions only"
        )
    disk = disk_references[0]
    if (
        disk.stable_id != plan.storage.disk.stable_id
        or disk.expected_size_bytes != plan.storage.disk.expected_size_bytes
        or disk.parent_reference_id
        or disk.start_bytes != 0
        or not SHA256_RE.fullmatch(disk.topology_digest)
        or graph.partition_table != "gpt"
    ):
        raise ManualStorageGraphError(
            "Manual disk reference does not match the selected GPT disk"
        )
    for item in partition_references:
        if (
            not item.stable_id
            or item.parent_reference_id != disk.reference_id
            or item.expected_size_bytes <= 0
            or item.start_bytes < 0
            or item.topology_digest != disk.topology_digest
        ):
            raise ManualStorageGraphError(
                "Manual graph contains an invalid partition reference"
            )
    if len(graph.boot_targets) != 1:
        raise ManualStorageGraphError("Manual graph requires one EFI boot target")
    boot = graph.boot_targets[0]
    if (
        boot.bios_disk_reference_id
        or boot.fallback_path
        or boot.vendor_directory != "EFI/AnduinOS"
    ):
        raise ManualStorageGraphError(
            "Manual boot target must use vendor-only UEFI writes"
        )
    forbidden = {
        StorageGraphAction.WRITE_BIOS_BOOTLOADER,
        StorageGraphAction.WRITE_FALLBACK_BOOT_FILES,
    }
    if any(item.action in forbidden for item in graph.operations):
        raise ManualStorageGraphError(
            "Manual graph contains an unsupported boot write"
        )

    replace_table = tuple(
        item
        for item in graph.operations
        if item.action is StorageGraphAction.REPLACE_PARTITION_TABLE
    )
    modify_table = tuple(
        item
        for item in graph.operations
        if item.action is StorageGraphAction.MODIFY_PARTITION_TABLE
    )
    preserve_targets = {
        item.target_id
        for item in graph.operations
        if item.action is StorageGraphAction.PRESERVE
    }
    delete_targets = {
        item.target_id
        for item in graph.operations
        if item.action is StorageGraphAction.DELETE_PARTITION
    }
    resize_targets = {
        item.target_id
        for item in graph.operations
        if item.action is StorageGraphAction.RESIZE_PARTITION
    }
    reference_targets = {item.reference_id for item in partition_references}
    if replace_table:
        if (
            len(replace_table) != 1
            or modify_table
            or partition_references
            or preserve_targets
            or delete_targets
            or resize_targets
            or graph.partition_resizes
        ):
            raise ManualStorageGraphError(
                "Replacement GPT cannot preserve or delete individual partitions"
            )
    elif (
        len(modify_table) != 1
        or preserve_targets & delete_targets
        or preserve_targets & resize_targets
        or delete_targets & resize_targets
        or preserve_targets | delete_targets | resize_targets
        != reference_targets
    ):
        raise ManualStorageGraphError(
            "Every existing manual partition must be preserved, deleted or resized"
        )

    resize_declarations = {
        item.target_reference_id: item for item in graph.partition_resizes
    }
    if (
        len(resize_declarations) != len(graph.partition_resizes)
        or set(resize_declarations) != resize_targets
    ):
        raise ManualStorageGraphError(
            "Manual resize declarations do not match resize operations"
        )
    references_by_id = {
        item.reference_id: item for item in partition_references
    }
    mib = 1024 * 1024
    for target_id, declaration in resize_declarations.items():
        reference = references_by_id[target_id]
        if (
            declaration.filesystem is not GraphFilesystem.NTFS
            or declaration.original_size_bytes != reference.expected_size_bytes
            or declaration.target_size_bytes % mib
            or declaration.target_size_bytes < 4 * 1024 * mib
            or declaration.target_size_bytes >= declaration.original_size_bytes
        ):
            raise ManualStorageGraphError(
                "Manual NTFS resize declaration is invalid"
            )

    roles = tuple(item.name for item in graph.partitions)
    allowed_roles = {item.value for item in ManualPartitionRole}
    if (
        any(item not in allowed_roles for item in roles)
        or len(roles) != len(set(roles))
        or roles.count(ManualPartitionRole.ROOT.value) != 1
    ):
        raise ManualStorageGraphError("Manual partition roles are invalid")
    previous_end = -1
    for item in graph.partitions:
        if (
            item.parent_reference_id != disk.reference_id
            or item.start_mib < 1
            or item.end_mib is None
            or item.end_mib <= item.start_mib
            or item.start_mib < previous_end
        ):
            raise ManualStorageGraphError("Manual partition geometry is invalid")
        previous_end = item.end_mib
    new_ids = {item.partition_id for item in graph.partitions}
    formatted = {
        item.target_id
        for item in graph.operations
        if item.action is StorageGraphAction.FORMAT
    }
    if formatted != new_ids:
        raise ManualStorageGraphError(
            "Manual mode formats every new partition and no existing partition"
        )


def validate_manual_storage_graph(
    plan: InstallPlan,
    inventory: StorageInventory,
) -> DiskInventory:
    """Rebuild a manual graph from current inventory or reject it as stale."""

    from .storage_graph_planning import validate_storage_graph

    validate_storage_graph(plan)
    graph = plan.storage.graph
    assert graph is not None
    if graph.mode is not StorageGraphMode.MANUAL:
        raise ManualStorageGraphError("Storage graph is not manual")
    disk_reference = next(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.DISK
    )
    try:
        disk = verify_disk_topology(
            DiskTopologyBinding(
                stable_id=disk_reference.stable_id,
                expected_size_bytes=disk_reference.expected_size_bytes,
                topology_digest=disk_reference.topology_digest,
            ),
            inventory,
        )
    except StaleStorageInventoryError as error:
        raise ManualStorageGraphError(str(error)) from error

    selection = _selection_from_graph(plan, disk, graph)
    expected = build_manual_storage_graph(
        plan,
        disk,
        selection,
        inventory_digest=graph.inventory_digest,
    )
    if graph != expected:
        raise ManualStorageGraphError(
            "Storage graph does not match the canonical manual layout"
        )
    return disk


def manual_selection_from_graph(
    plan: InstallPlan,
    disk: DiskInventory,
) -> ManualStorageSelection:
    graph = plan.storage.graph
    if graph is None or graph.mode is not StorageGraphMode.MANUAL:
        raise ManualStorageGraphError("Manual storage graph is missing")
    return _selection_from_graph(plan, disk, graph)


def _selection_from_graph(
    plan: InstallPlan,
    disk: DiskInventory,
    graph: StorageGraph,
) -> ManualStorageSelection:
    references = {
        item.reference_id: item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.PARTITION
    }
    deleted_ids = {
        item.target_id
        for item in graph.operations
        if item.action is StorageGraphAction.DELETE_PARTITION
    }
    resize_declarations = {
        item.target_reference_id: item for item in graph.partition_resizes
    }
    reinitialize = any(
        item.action is StorageGraphAction.REPLACE_PARTITION_TABLE
        for item in graph.operations
    )
    esp_id = graph.boot_targets[0].efi_filesystem_id
    reused_esp = references[esp_id].stable_id if esp_id in references else ""
    requests = tuple(
        ManualPartitionRequest(
            role=ManualPartitionRole(item.name),
            start_mib=item.start_mib,
            end_mib=(
                item.end_mib
                if item.end_mib is not None
                else item.start_mib
            ),
        )
        for item in graph.partitions
    )
    return ManualStorageSelection(
        disk_stable_id=disk.identity.stable_id,
        disk_size_bytes=disk.identity.expected_size_bytes,
        disk_topology_digest=disk.topology_digest,
        reinitialize_gpt=reinitialize,
        deleted_partuuids=tuple(
            item.identity.partuuid
            for item in disk.partitions
            if _existing_partition_reference_id(
                _disk_id(disk.identity.stable_id),
                item.identity.partuuid,
            )
            in deleted_ids
        ),
        reused_esp_partuuid=reused_esp,
        filesystem=plan.storage.filesystem,
        new_partitions=requests,
        resized_partitions=tuple(
            ManualPartitionResizeRequest(
                partuuid=item.identity.partuuid,
                original_size_bytes=(
                    resize_declarations[
                        _existing_partition_reference_id(
                            _disk_id(disk.identity.stable_id),
                            item.identity.partuuid,
                        )
                    ].original_size_bytes
                ),
                target_size_mib=(
                    resize_declarations[
                        _existing_partition_reference_id(
                            _disk_id(disk.identity.stable_id),
                            item.identity.partuuid,
                        )
                    ].target_size_bytes
                    // (1024 * 1024)
                ),
            )
            for item in disk.partitions
            if _existing_partition_reference_id(
                _disk_id(disk.identity.stable_id),
                item.identity.partuuid,
            )
            in resize_declarations
        ),
    )


def _disk_id(stable_id: str) -> str:
    return f"disk:{stable_id}"


def _existing_partition_reference_id(disk_id: str, partuuid: str) -> str:
    return f"{disk_id}:existing-partition:{partuuid}"


def _partition_reference(
    disk_id: str,
    topology_digest: str,
    partition: PartitionInventory,
) -> BlockReference:
    return BlockReference(
        reference_id=_existing_partition_reference_id(
            disk_id,
            partition.identity.partuuid,
        ),
        kind=BlockReferenceKind.PARTITION,
        stable_id=partition.identity.partuuid,
        parent_reference_id=disk_id,
        expected_size_bytes=partition.identity.size_bytes,
        start_bytes=partition.identity.start_bytes,
        topology_digest=topology_digest,
    )


def _next_partition_numbers(used: set[int], count: int) -> tuple[int, ...]:
    result: list[int] = []
    candidate = 1
    while len(result) < count:
        if candidate not in used:
            result.append(candidate)
        candidate += 1
    return tuple(result)


def _mount_role(mount_point: str) -> MountRole:
    return {
        "/": MountRole.ROOT,
        "/home": MountRole.HOME,
        "/var/log": MountRole.LOG,
        "/.snapshots": MountRole.SNAPSHOTS,
        "/var/lib/containers": MountRole.CONTAINERS,
        "/var/lib/libvirt/images": MountRole.VIRTUAL_MACHINES,
    }[mount_point]
