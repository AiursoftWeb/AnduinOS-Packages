"""Build, validate and resolve the versioned storage graph."""

from __future__ import annotations

import re
from dataclasses import replace

from .btrfs import BTRFS_SUBVOLUMES
from .coexistence import (
    GUIDED_ROOT_MINIMUM_BYTES,
    MIB,
    CoexistenceStatus,
    analyze_guided_coexistence,
)
from .layout import build_erase_disk_layout
from .model import Architecture, DiskIdentity, Filesystem, InstallMode, InstallPlan
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
    FreeExtent,
    PartitionInventory,
    StaleStorageInventoryError,
    StorageInventory,
    verify_disk_topology,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StorageGraphValidationError(ValueError):
    pass


def build_guided_coexistence_storage_graph(
    plan: InstallPlan,
    disk: DiskInventory,
    extent: FreeExtent,
    *,
    inventory_digest: str,
    reused_esp: PartitionInventory | None,
) -> StorageGraph:
    """Describe a free-space-only coexistence layout without commands."""

    if plan.storage.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise ValueError("Plan is not guided coexistence")
    if (
        plan.storage.disk.stable_id != disk.identity.stable_id
        or plan.storage.disk.expected_size_bytes
        != disk.identity.expected_size_bytes
    ):
        raise ValueError("Inventory disk does not match the selected disk")
    decision = analyze_guided_coexistence(disk, plan.platform.firmware)
    if decision.status is not CoexistenceStatus.AVAILABLE:
        raise ValueError("Selected disk is not eligible for guided coexistence")
    candidate = next(
        (
            item
            for item in decision.free_space_candidates
            if item.extent.extent_id == extent.extent_id
        ),
        None,
    )
    if candidate is None:
        raise ValueError("Selected free extent is not eligible")
    if reused_esp is not None and reused_esp not in decision.esp_candidates:
        raise ValueError("Selected EFI System Partition is not reusable")
    if candidate.requires_reused_esp and reused_esp is None:
        raise ValueError("Selected free extent requires a reusable ESP")

    disk_id = _disk_id(disk.identity.stable_id)
    existing_references = tuple(
        _partition_reference(disk_id, disk.topology_digest, item)
        for item in disk.partitions
    )
    extent_reference = BlockReference(
        reference_id=_free_extent_reference_id(extent),
        kind=BlockReferenceKind.FREE_EXTENT,
        stable_id=extent.extent_id,
        parent_reference_id=disk_id,
        expected_size_bytes=extent.size_bytes,
        start_bytes=extent.start_bytes,
        topology_digest=disk.topology_digest,
    )
    disk_reference = BlockReference(
        reference_id=disk_id,
        kind=BlockReferenceKind.DISK,
        stable_id=disk.identity.stable_id,
        parent_reference_id="",
        expected_size_bytes=disk.identity.expected_size_bytes,
        start_bytes=0,
        topology_digest=disk.topology_digest,
    )

    start_mib = _ceil_div(extent.start_bytes, MIB)
    end_mib = (extent.end_bytes + 1) // MIB
    swap_names = ("swap",) if plan.storage.swap_size_mib else ()
    names = (
        ("efi-system", *swap_names, "root")
        if reused_esp is None
        else (*swap_names, "root")
    )
    numbers = _next_partition_numbers(
        {item.identity.number for item in disk.partitions},
        len(names),
    )
    sizes_mib = {"efi-system": plan.storage.esp_size_mib}
    if plan.storage.swap_size_mib:
        sizes_mib["swap"] = plan.storage.swap_size_mib
    cursor = start_mib
    partitions: list[PartitionDeclaration] = []
    partition_ids: dict[str, str] = {}
    for name, number in zip(names, numbers, strict=True):
        partition_id = f"{disk_id}:new-partition:{number}"
        partition_ids[name] = partition_id
        part_end = (
            cursor + sizes_mib[name]
            if name in sizes_mib
            else end_mib
        )
        partitions.append(
            PartitionDeclaration(
                partition_id=partition_id,
                parent_reference_id=extent_reference.reference_id,
                number=number,
                name=name,
                start_mib=cursor,
                end_mib=part_end,
                flags=(
                    ("esp",)
                    if name == "efi-system"
                    else (("swap",) if name == "swap" else ())
                ),
            )
        )
        cursor = part_end
    root = next(item for item in partitions if item.name == "root")
    if root.end_mib is None or (
        root.end_mib - root.start_mib
    ) * MIB < GUIDED_ROOT_MINIMUM_BYTES:
        raise ValueError("Selected free extent leaves less than 20 GiB for root")

    if reused_esp is None:
        esp_id = partition_ids["efi-system"]
    else:
        esp_id = _existing_partition_reference_id(
            disk_id, reused_esp.identity.partuuid
        )
    filesystems: list[FilesystemDeclaration] = []
    if reused_esp is None:
        filesystems.append(
            FilesystemDeclaration(
                filesystem_id=esp_id,
                block_id=esp_id,
                filesystem=GraphFilesystem.VFAT,
                label="ANDUIN_EFI",
            )
        )
    else:
        filesystems.append(
            FilesystemDeclaration(
                filesystem_id=esp_id,
                block_id=esp_id,
                filesystem=GraphFilesystem.VFAT,
                label="",
            )
        )
    if "swap" in partition_ids:
        filesystems.append(
            FilesystemDeclaration(
                filesystem_id=partition_ids["swap"],
                block_id=partition_ids["swap"],
                filesystem=GraphFilesystem.SWAP,
                label="AnduinOS-swap",
            )
        )
    filesystems.append(
        FilesystemDeclaration(
            filesystem_id=partition_ids["root"],
            block_id=partition_ids["root"],
            filesystem=GraphFilesystem(plan.storage.filesystem.value),
            label="AnduinOS",
        )
    )

    subvolumes: tuple[SubvolumeDeclaration, ...] = ()
    capabilities = [StorageCapability.BOOTABLE]
    if plan.storage.filesystem is Filesystem.BTRFS:
        subvolumes = tuple(
            SubvolumeDeclaration(
                subvolume_id=(
                    f"{partition_ids['root']}:subvolume:{item.name}"
                ),
                filesystem_id=partition_ids["root"],
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
            (StorageCapability.SYSTEM_ROLLBACK, StorageCapability.SNAPSHOT_MANAGEMENT)
        )
    else:
        mounts = (
            MountDeclaration(
                source_id=partition_ids["root"],
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

    operations = [
        *(
            StorageGraphOperation(
                StorageGraphAction.PRESERVE, item.reference_id
            )
            for item in existing_references
        ),
        StorageGraphOperation(
            StorageGraphAction.MODIFY_PARTITION_TABLE, disk_id
        ),
        *(
            StorageGraphOperation(
                StorageGraphAction.CREATE_PARTITION, item.partition_id
            )
            for item in partitions
        ),
        *(
            StorageGraphOperation(
                StorageGraphAction.FORMAT, item.partition_id
            )
            for item in partitions
        ),
        *(
            StorageGraphOperation(
                StorageGraphAction.CREATE_SUBVOLUME, item.subvolume_id
            )
            for item in subvolumes
        ),
        StorageGraphOperation(
            StorageGraphAction.CONFIGURE_MOUNTS, partition_ids["root"]
        ),
        StorageGraphOperation(StorageGraphAction.WRITE_BOOT_FILES, esp_id),
        StorageGraphOperation(StorageGraphAction.UPDATE_NVRAM, esp_id),
    ]
    return StorageGraph(
        schema_version=STORAGE_GRAPH_SCHEMA_VERSION,
        mode=StorageGraphMode.GUIDED_COEXISTENCE,
        inventory_digest=inventory_digest,
        partition_table="gpt",
        block_references=(
            disk_reference,
            *existing_references,
            extent_reference,
        ),
        partitions=tuple(partitions),
        partition_resizes=(),
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


def build_erase_disk_storage_graph(
    plan: InstallPlan,
    binding: DiskTopologyBinding,
    inventory_digest: str,
) -> StorageGraph:
    """Map the existing erase-disk contract into the current graph schema."""

    if plan.storage.mode is not InstallMode.ERASE_DISK:
        raise ValueError("Only erase-disk storage graphs are implemented")
    disk = plan.storage.disk
    if (
        binding.stable_id != disk.stable_id
        or binding.expected_size_bytes != disk.expected_size_bytes
    ):
        raise ValueError("Topology binding does not identify the selected disk")

    layout = build_erase_disk_layout(plan)
    disk_id = _disk_id(disk.stable_id)
    partition_ids = {
        item.name: _partition_id(disk_id, item.number)
        for item in layout.partitions
    }
    partitions = tuple(
        PartitionDeclaration(
            partition_id=partition_ids[item.name],
            parent_reference_id=disk_id,
            number=item.number,
            name=item.name,
            start_mib=item.start_mib,
            end_mib=item.end_mib,
            flags=item.flags,
        )
        for item in layout.partitions
    )
    filesystems = [
        FilesystemDeclaration(
            filesystem_id=partition_ids["efi-system"],
            block_id=partition_ids["efi-system"],
            filesystem=GraphFilesystem.VFAT,
            label="ANDUIN_EFI",
        ),
    ]
    if "swap" in partition_ids:
        filesystems.append(
            FilesystemDeclaration(
                filesystem_id=partition_ids["swap"],
                block_id=partition_ids["swap"],
                filesystem=GraphFilesystem.SWAP,
                label="AnduinOS-swap",
            )
        )
    filesystems.append(
        FilesystemDeclaration(
            filesystem_id=partition_ids["root"],
            block_id=partition_ids["root"],
            filesystem=GraphFilesystem(plan.storage.filesystem.value),
            label="AnduinOS",
        )
    )

    subvolumes = ()
    mounts: tuple[MountDeclaration, ...]
    capabilities = [StorageCapability.BOOTABLE]
    if plan.storage.filesystem is Filesystem.BTRFS:
        subvolumes = tuple(
            SubvolumeDeclaration(
                subvolume_id=(
                    f"{partition_ids['root']}:subvolume:{item.name}"
                ),
                filesystem_id=partition_ids["root"],
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
            (StorageCapability.SYSTEM_ROLLBACK, StorageCapability.SNAPSHOT_MANAGEMENT)
        )
    else:
        mounts = (
            MountDeclaration(
                source_id=partition_ids["root"],
                target_path="/",
                role=MountRole.ROOT,
            ),
        )
    mounts += (
        MountDeclaration(
            source_id=partition_ids["efi-system"],
            target_path="/boot/efi",
            role=MountRole.EFI,
        ),
    )

    fallback = (
        "EFI/BOOT/BOOTX64.EFI"
        if plan.platform.architecture is Architecture.AMD64
        else "EFI/BOOT/BOOTAA64.EFI"
    )
    boot_targets = (
        BootTarget(
            efi_filesystem_id=partition_ids["efi-system"],
            bios_disk_reference_id=(
                disk_id
                if plan.platform.architecture is Architecture.AMD64
                else ""
            ),
            vendor_directory="EFI/AnduinOS",
            fallback_path=fallback,
        ),
    )

    operations = [
        StorageGraphOperation(
            StorageGraphAction.REPLACE_PARTITION_TABLE, disk_id
        )
    ]
    operations.extend(
        StorageGraphOperation(
            StorageGraphAction.CREATE_PARTITION, item.partition_id
        )
        for item in partitions
    )
    format_names = ["efi-system"]
    if "swap" in partition_ids:
        format_names.append("swap")
    format_names.append("root")
    operations.extend(
        StorageGraphOperation(StorageGraphAction.FORMAT, partition_ids[name])
        for name in format_names
    )
    operations.extend(
        StorageGraphOperation(
            StorageGraphAction.CREATE_SUBVOLUME, item.subvolume_id
        )
        for item in subvolumes
    )
    operations.append(
        StorageGraphOperation(
            StorageGraphAction.CONFIGURE_MOUNTS, partition_ids["root"]
        )
    )
    if plan.platform.architecture is Architecture.AMD64:
        operations.append(
            StorageGraphOperation(
                StorageGraphAction.WRITE_BIOS_BOOTLOADER, disk_id
            )
        )
    operations.extend(
        (
            StorageGraphOperation(
                StorageGraphAction.WRITE_BOOT_FILES,
                partition_ids["efi-system"],
            ),
            StorageGraphOperation(
                StorageGraphAction.WRITE_FALLBACK_BOOT_FILES,
                partition_ids["efi-system"],
            ),
        )
    )

    return StorageGraph(
        schema_version=STORAGE_GRAPH_SCHEMA_VERSION,
        mode=StorageGraphMode.ERASE_DISK,
        inventory_digest=inventory_digest,
        partition_table=layout.table,
        block_references=(
            BlockReference(
                reference_id=disk_id,
                kind=BlockReferenceKind.DISK,
                stable_id=binding.stable_id,
                parent_reference_id="",
                expected_size_bytes=binding.expected_size_bytes,
                start_bytes=0,
                topology_digest=binding.topology_digest,
            ),
        ),
        partitions=partitions,
        partition_resizes=(),
        filesystems=tuple(filesystems),
        subvolumes=subvolumes,
        mounts=mounts,
        boot_targets=boot_targets,
        operations=tuple(operations),
        requested_capabilities=tuple(capabilities),
    )


def validate_storage_graph(plan: InstallPlan) -> None:
    """Require the canonical graph for the currently supported mode."""

    graph = plan.storage.graph
    if graph is None:
        raise StorageGraphValidationError("Storage graph is required")
    if graph.schema_version != STORAGE_GRAPH_SCHEMA_VERSION:
        raise StorageGraphValidationError(
            "Unsupported storage graph schema version "
            f"{graph.schema_version}; expected {STORAGE_GRAPH_SCHEMA_VERSION}"
        )
    if graph.mode.value != plan.storage.mode.value:
        raise StorageGraphValidationError("Storage graph mode does not match")
    if not SHA256_RE.fullmatch(graph.inventory_digest):
        raise StorageGraphValidationError("Invalid storage inventory digest")
    if graph.mode is StorageGraphMode.GUIDED_COEXISTENCE:
        _validate_guided_graph_structure(plan, graph)
        return
    if graph.mode is StorageGraphMode.MANUAL:
        from .manual_graph_planning import validate_manual_graph_structure

        validate_manual_graph_structure(plan, graph)
        return
    if len(graph.block_references) != 1:
        raise StorageGraphValidationError(
            "Erase-disk graph requires exactly one disk reference"
        )
    reference = graph.block_references[0]
    if reference.kind is not BlockReferenceKind.DISK:
        raise StorageGraphValidationError(
            "Erase-disk graph reference must be a disk"
        )
    if not SHA256_RE.fullmatch(reference.topology_digest):
        raise StorageGraphValidationError("Invalid disk topology digest")
    expected = build_erase_disk_storage_graph(
        plan,
        DiskTopologyBinding(
            stable_id=reference.stable_id,
            expected_size_bytes=reference.expected_size_bytes,
            topology_digest=reference.topology_digest,
        ),
        graph.inventory_digest,
    )
    if graph != expected:
        raise StorageGraphValidationError(
            "Storage graph does not match the canonical erase-disk plan"
        )


def validate_guided_coexistence_graph(
    plan: InstallPlan,
    inventory: StorageInventory,
) -> DiskInventory:
    """Rebuild a guided graph from current inventory or reject it as stale."""

    validate_storage_graph(plan)
    graph = plan.storage.graph
    assert graph is not None
    if graph.mode is not StorageGraphMode.GUIDED_COEXISTENCE:
        raise StorageGraphValidationError("Storage graph is not coexistence")
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
        raise StorageGraphValidationError(str(error)) from error
    extent_reference = next(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.FREE_EXTENT
    )
    extent = next(
        (
            item
            for item in disk.free_extents
            if item.extent_id == extent_reference.stable_id
            and item.start_bytes == extent_reference.start_bytes
            and item.size_bytes == extent_reference.expected_size_bytes
        ),
        None,
    )
    if extent is None:
        raise StorageGraphValidationError("Selected free extent changed")

    boot_target = graph.boot_targets[0]
    existing_references = {
        item.reference_id: item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.PARTITION
    }
    reused_esp = None
    selected_esp_reference = existing_references.get(
        boot_target.efi_filesystem_id
    )
    if selected_esp_reference is not None:
        reused_esp = next(
            (
                item
                for item in disk.partitions
                if item.identity.partuuid == selected_esp_reference.stable_id
            ),
            None,
        )
        if reused_esp is None:
            raise StorageGraphValidationError(
                "Selected EFI System Partition changed"
            )

    expected = build_guided_coexistence_storage_graph(
        plan,
        disk,
        extent,
        inventory_digest=graph.inventory_digest,
        reused_esp=reused_esp,
    )
    if graph != expected:
        raise StorageGraphValidationError(
            "Storage graph does not match the canonical coexistence plan"
        )
    return disk


def resolve_storage_graph(
    plan: InstallPlan, inventory: StorageInventory
) -> InstallPlan:
    """Resolve the selected stable identity to the executor's current path."""

    validate_storage_graph(plan)
    graph = plan.storage.graph
    assert graph is not None
    reference = graph.block_references[0]
    try:
        current = verify_disk_topology(
            DiskTopologyBinding(
                stable_id=reference.stable_id,
                expected_size_bytes=reference.expected_size_bytes,
                topology_digest=reference.topology_digest,
            ),
            inventory,
        )
    except StaleStorageInventoryError as error:
        raise StorageGraphValidationError(str(error)) from error
    resolved = DiskIdentity(
        path=current.identity.path,
        stable_id=current.identity.stable_id,
        expected_size_bytes=current.identity.expected_size_bytes,
        model=current.identity.model,
        serial=current.identity.serial,
    )
    return replace(plan, storage=replace(plan.storage, disk=resolved))


def _disk_id(stable_id: str) -> str:
    return f"disk:{stable_id}"


def _partition_id(disk_id: str, number: int) -> str:
    return f"{disk_id}:partition:{number}"


def _existing_partition_reference_id(disk_id: str, partuuid: str) -> str:
    return f"{disk_id}:existing-partition:{partuuid}"


def _free_extent_reference_id(extent: FreeExtent) -> str:
    return f"free-extent:{extent.extent_id}"


def _partition_reference(
    disk_id: str,
    topology_digest: str,
    partition: PartitionInventory,
) -> BlockReference:
    return BlockReference(
        reference_id=_existing_partition_reference_id(
            disk_id, partition.identity.partuuid
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


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _validate_guided_graph_structure(
    plan: InstallPlan,
    graph: StorageGraph,
) -> None:
    disk_references = tuple(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.DISK
    )
    extent_references = tuple(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.FREE_EXTENT
    )
    partition_references = tuple(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.PARTITION
    )
    if len(disk_references) != 1 or len(extent_references) != 1:
        raise StorageGraphValidationError(
            "Coexistence graph requires one disk and one free extent"
        )
    if len(graph.block_references) != (
        2 + len(partition_references)
    ):
        raise StorageGraphValidationError(
            "Coexistence graph contains an unsupported reference kind"
        )
    disk = disk_references[0]
    extent = extent_references[0]
    if (
        disk.stable_id != plan.storage.disk.stable_id
        or disk.expected_size_bytes
        != plan.storage.disk.expected_size_bytes
        or disk.parent_reference_id
        or disk.start_bytes != 0
        or not SHA256_RE.fullmatch(disk.topology_digest)
    ):
        raise StorageGraphValidationError(
            "Coexistence disk reference does not match the selected disk"
        )
    if (
        graph.partition_table != "gpt"
        or extent.parent_reference_id != disk.reference_id
        or extent.expected_size_bytes <= 0
        or extent.start_bytes < 0
        or extent.topology_digest != disk.topology_digest
        or not SHA256_RE.fullmatch(extent.stable_id)
    ):
        raise StorageGraphValidationError("Invalid coexistence free extent")
    for item in partition_references:
        if (
            not item.stable_id
            or item.parent_reference_id != disk.reference_id
            or item.expected_size_bytes <= 0
            or item.start_bytes < 0
            or item.topology_digest != disk.topology_digest
        ):
            raise StorageGraphValidationError(
                "Invalid preserved partition reference"
            )
    if len(graph.boot_targets) != 1:
        raise StorageGraphValidationError(
            "Coexistence graph requires one EFI boot target"
        )
    boot = graph.boot_targets[0]
    if (
        boot.bios_disk_reference_id
        or boot.fallback_path
        or boot.vendor_directory != "EFI/AnduinOS"
    ):
        raise StorageGraphValidationError(
            "Coexistence boot target cannot use BIOS or fallback paths"
        )
    preserved_targets = tuple(
        item.target_id
        for item in graph.operations
        if item.action is StorageGraphAction.PRESERVE
    )
    if preserved_targets != tuple(
        item.reference_id for item in partition_references
    ):
        raise StorageGraphValidationError(
            "Every existing partition must be explicitly preserved"
        )
    forbidden = {
        StorageGraphAction.REPLACE_PARTITION_TABLE,
        StorageGraphAction.WRITE_BIOS_BOOTLOADER,
        StorageGraphAction.WRITE_FALLBACK_BOOT_FILES,
    }
    if any(item.action in forbidden for item in graph.operations):
        raise StorageGraphValidationError(
            "Coexistence graph contains a whole-disk or fallback write"
        )
    extent_start_mib = _ceil_div(extent.start_bytes, MIB)
    extent_end_mib = (
        extent.start_bytes + extent.expected_size_bytes
    ) // MIB
    previous_end = extent_start_mib
    for item in graph.partitions:
        if (
            item.parent_reference_id != extent.reference_id
            or item.start_mib < previous_end
            or item.end_mib is None
            or item.end_mib <= item.start_mib
            or item.end_mib > extent_end_mib
        ):
            raise StorageGraphValidationError(
                "Created partition escapes the selected free extent"
            )
        previous_end = item.end_mib


def _mount_role(mount_point: str) -> MountRole:
    return {
        "/": MountRole.ROOT,
        "/home": MountRole.HOME,
        "/var/log": MountRole.LOG,
        "/.snapshots": MountRole.SNAPSHOTS,
        "/var/lib/containers": MountRole.CONTAINERS,
        "/var/lib/libvirt/images": MountRole.VIRTUAL_MACHINES,
    }[mount_point]
