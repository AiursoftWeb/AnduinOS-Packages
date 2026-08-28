"""Pure state model for the gated GTK storage workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from languages import DEFAULT_KEYBOARD, DEFAULT_LOCALE, DEFAULT_TIMEZONE

from .coexistence import (
    CoexistenceStatus,
    CoexistenceDecision,
    analyze_guided_coexistence,
)
from .model import (
    AccessSpec,
    SCHEMA_VERSION,
    AuthenticationMode,
    BootSpec,
    DiskIdentity,
    Filesystem,
    IdentitySpec,
    InstallMode,
    InstallPlan,
    KeyboardSpec,
    MokPasswordPolicy,
    PlatformSpec,
    RegionalSpec,
    SecureBoot,
    SourceSpec,
    StorageSpec,
)
from .manual_graph_planning import build_manual_storage_graph
from .manual_layout import (
    MIB,
    ManualFreeExtent,
    ManualPartitionRole,
    ManualStorageSelection,
    manual_available_extents,
    resize_for_partuuid,
    validate_manual_selection,
)
from .manual_write_set import build_manual_storage_write_set
from .probe import PlatformProbe
from .storage_graph import StorageGraph
from .storage_graph_planning import build_guided_coexistence_storage_graph
from .storage_inventory import (
    EFI_SYSTEM_PARTITION_GUID,
    DiskInventory,
    FreeExtent,
    PartitionIdentity,
    PartitionInventory,
    StorageInventory,
)
from .swap_policy import (
    SwapSizing,
    calculate_swap_sizing,
    probe_physical_memory_bytes,
    validate_disk_swap_selection,
)
from .storage_write_set import (
    StorageAction,
    StorageWriteSet,
    build_guided_coexistence_write_set,
)
from .validation import MINIMUM_DISK_BYTES


@dataclass(frozen=True)
class StorageDiskChoice:
    disk: DiskInventory
    coexistence: CoexistenceDecision
    is_live_media: bool
    erase_available: bool

    @property
    def guided_available(self) -> bool:
        return (
            not self.is_live_media
            and self.coexistence.status is CoexistenceStatus.AVAILABLE
        )


@dataclass(frozen=True)
class StorageWorkflow:
    inventory: StorageInventory
    platform: PlatformProbe
    physical_memory_bytes: int
    disks: tuple[StorageDiskChoice, ...]

    def disk(self, stable_id: str) -> StorageDiskChoice:
        for item in self.disks:
            if item.disk.identity.stable_id == stable_id:
                return item
        raise KeyError(stable_id)


@dataclass(frozen=True)
class GuidedStorageSelection:
    disk_stable_id: str
    disk_size_bytes: int
    free_extent_id: str
    reused_esp_partuuid: str
    filesystem: Filesystem


@dataclass(frozen=True)
class GuidedStoragePreview:
    selection: GuidedStorageSelection
    graph: StorageGraph
    write_set: StorageWriteSet
    disk: DiskInventory
    extent: FreeExtent
    reused_esp: PartitionInventory | None
    swap_sizing: SwapSizing
    swap_size_mib: int


@dataclass(frozen=True)
class GuidedPartitionConfirmation:
    name: str
    display_path: str
    start_mib: int
    end_mib: int


@dataclass(frozen=True)
class GuidedFormatConfirmation:
    display_path: str
    filesystem: str


@dataclass(frozen=True)
class GuidedStorageConfirmation:
    preserved_paths: tuple[str, ...]
    new_partitions: tuple[GuidedPartitionConfirmation, ...]
    formats: tuple[GuidedFormatConfirmation, ...]
    reused_esp_path: str
    writes_vendor_boot_files: bool
    writes_shared_fallback: bool
    updates_nvram: bool


@dataclass(frozen=True)
class ManualStoragePreview:
    selection: ManualStorageSelection
    graph: StorageGraph
    write_set: StorageWriteSet
    disk: DiskInventory
    available_extents: tuple[ManualFreeExtent, ...]


@dataclass(frozen=True)
class ManualStorageConfirmation:
    reinitializes_gpt: bool
    preserved_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    resized_partitions: tuple["ManualResizeConfirmation", ...]
    new_partitions: tuple[GuidedPartitionConfirmation, ...]
    formats: tuple[GuidedFormatConfirmation, ...]
    reused_esp_path: str
    writes_vendor_boot_files: bool
    writes_shared_fallback: bool
    updates_nvram: bool


@dataclass(frozen=True)
class ManualResizeConfirmation:
    display_path: str
    original_size_bytes: int
    target_size_bytes: int
    reclaimed_bytes: int


class ManualDiskSegmentKind(str, Enum):
    PRESERVED = "preserved"
    RESIZED = "resized"
    DELETED = "deleted"
    NEW_ESP = "new-esp"
    NEW_ROOT = "new-root"
    NEW_SWAP = "new-swap"
    FREE = "free"


@dataclass(frozen=True)
class ManualDiskSegment:
    segment_id: str
    title: str
    detail: str
    start_mib: int
    end_mib: int
    kind: ManualDiskSegmentKind

    @property
    def size_mib(self) -> int:
        return self.end_mib - self.start_mib


@dataclass(frozen=True)
class ManualDiskMap:
    current: tuple[ManualDiskSegment, ...]
    planned: tuple[ManualDiskSegment, ...]


def build_manual_disk_map(
    disk: DiskInventory,
    selection: ManualStorageSelection,
) -> ManualDiskMap:
    """Describe the current and planned disk maps without GTK state."""

    deleted = set(selection.deleted_partuuids)
    current = [
        ManualDiskSegment(
            segment_id=f"existing:{item.identity.partuuid}",
            title=item.identity.path,
            detail=(
                item.filesystem_label
                or item.filesystem_type
                or "Unknown"
            ),
            start_mib=(item.identity.start_bytes + MIB - 1) // MIB,
            end_mib=(
                item.identity.start_bytes + item.identity.size_bytes
            )
            // MIB,
            kind=(
                ManualDiskSegmentKind.DELETED
                if selection.reinitialize_gpt
                or item.identity.partuuid in deleted
                else ManualDiskSegmentKind.PRESERVED
            ),
        )
        for item in disk.partitions
    ]
    current.extend(
        ManualDiskSegment(
            segment_id=f"current-free:{item.extent_id}",
            title="Unallocated",
            detail="Free space",
            start_mib=(item.start_bytes + MIB - 1) // MIB,
            end_mib=(item.start_bytes + item.size_bytes) // MIB,
            kind=ManualDiskSegmentKind.FREE,
        )
        for item in disk.free_extents
        if (item.start_bytes + item.size_bytes) // MIB
        > (item.start_bytes + MIB - 1) // MIB
    )

    planned = []
    if not selection.reinitialize_gpt:
        planned.extend(
            ManualDiskSegment(
                segment_id=f"preserved:{item.identity.partuuid}",
                title=item.identity.path,
                detail=(
                    (
                        (item.filesystem_label + " · ")
                        if item.filesystem_label
                        else ""
                    )
                    + (
                        "NTFS · Resized"
                        if resize_for_partuuid(
                            selection, item.identity.partuuid
                        )
                        else item.filesystem_type or "Unknown"
                    )
                ),
                start_mib=(item.identity.start_bytes + MIB - 1) // MIB,
                end_mib=(
                    item.identity.start_bytes
                    + (
                        resized.target_size_mib * MIB
                        if (
                            resized := resize_for_partuuid(
                                selection, item.identity.partuuid
                            )
                        )
                        else item.identity.size_bytes
                    )
                )
                // MIB,
                kind=(
                    ManualDiskSegmentKind.RESIZED
                    if resize_for_partuuid(
                        selection, item.identity.partuuid
                    )
                    else ManualDiskSegmentKind.PRESERVED
                ),
            )
            for item in disk.partitions
            if item.identity.partuuid not in deleted
        )
    kind_by_role = {
        ManualPartitionRole.EFI_SYSTEM: ManualDiskSegmentKind.NEW_ESP,
        ManualPartitionRole.ROOT: ManualDiskSegmentKind.NEW_ROOT,
        ManualPartitionRole.SWAP: ManualDiskSegmentKind.NEW_SWAP,
    }
    planned.extend(
        ManualDiskSegment(
            segment_id=f"new:{item.role.value}",
            title=item.role.value,
            detail="New partition",
            start_mib=item.start_mib,
            end_mib=item.end_mib,
            kind=kind_by_role[item.role],
        )
        for item in selection.new_partitions
    )
    planned.extend(
        ManualDiskSegment(
            segment_id=f"planned-free:{item.start_mib}:{item.end_mib}",
            title="Unallocated",
            detail="Free space",
            start_mib=item.start_mib,
            end_mib=item.end_mib,
            kind=ManualDiskSegmentKind.FREE,
        )
        for item in manual_available_extents(disk, selection)
    )
    return ManualDiskMap(
        current=tuple(sorted(current, key=_manual_segment_sort_key)),
        planned=tuple(sorted(planned, key=_manual_segment_sort_key)),
    )


def _manual_segment_sort_key(
    item: ManualDiskSegment,
) -> tuple[int, int, str]:
    return item.start_mib, item.end_mib, item.segment_id


def build_development_storage_workflow(
    platform: PlatformProbe,
) -> StorageWorkflow:
    """Return a useful synthetic disk for non-destructive UI development."""

    mib = 1024 * 1024
    gib = 1024 * mib
    disk_id = "serial:anduinos-development-disk"
    topology_digest = "d" * 64
    disk = DiskInventory(
        identity=DiskIdentity(
            path="/dev/vda",
            stable_id=disk_id,
            expected_size_bytes=128 * gib,
            model="AnduinOS Development Disk (simulated)",
            serial="ANDUINOS-DEV-0001",
        ),
        partition_table="gpt",
        partition_table_uuid="anduinos-development-gpt",
        partitions=(
            PartitionInventory(
                identity=PartitionIdentity(
                    path="/dev/vda1",
                    number=1,
                    partuuid="anduinos-development-esp",
                    start_bytes=mib,
                    size_bytes=1024 * mib,
                ),
                parent_disk_id=disk_id,
                partition_type=EFI_SYSTEM_PARTITION_GUID,
                filesystem_type="vfat",
                filesystem_uuid="ANDUINOS-DEV-ESP",
                filesystem_label="SYSTEM",
                flags=("boot", "esp"),
            ),
            PartitionInventory(
                identity=PartitionIdentity(
                    path="/dev/vda2",
                    number=2,
                    partuuid="anduinos-development-windows",
                    start_bytes=1025 * mib,
                    size_bytes=(64 * 1024 - 1025) * mib,
                ),
                parent_disk_id=disk_id,
                partition_type=(
                    "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
                ),
                filesystem_type="ntfs",
                filesystem_uuid="ANDUINOS-DEV-WINDOWS",
                filesystem_label="Windows",
            ),
            PartitionInventory(
                identity=PartitionIdentity(
                    path="/dev/vda3",
                    number=3,
                    partuuid="anduinos-development-data",
                    start_bytes=64 * gib,
                    size_bytes=16 * gib,
                ),
                parent_disk_id=disk_id,
                partition_type="0fc63daf-8483-4772-8e79-3d69d8477de4",
                filesystem_type="ext4",
                filesystem_uuid="ANDUINOS-DEV-DATA",
                filesystem_label="Data",
            ),
        ),
        free_extents=(
            FreeExtent(
                parent_disk_id=disk_id,
                start_bytes=80 * gib,
                size_bytes=(48 * 1024 - 1) * mib,
            ),
        ),
        topology_digest=topology_digest,
    )
    return build_storage_workflow(
        StorageInventory((disk,), "e" * 64),
        platform,
        physical_memory_probe=lambda: 8 * gib,
    )


def build_storage_workflow(
    inventory: StorageInventory,
    platform: PlatformProbe,
    *,
    live_device: str = "",
    physical_memory_probe=probe_physical_memory_bytes,
) -> StorageWorkflow:
    physical_memory_bytes = physical_memory_probe()
    choices = tuple(
        StorageDiskChoice(
            disk=disk,
            coexistence=analyze_guided_coexistence(
                disk, platform.firmware
            ),
            is_live_media=disk.identity.path == live_device,
            erase_available=(
                disk.identity.path != live_device
                and disk.identity.expected_size_bytes >= MINIMUM_DISK_BYTES
            ),
        )
        for disk in inventory.disks
    )
    return StorageWorkflow(
        inventory, platform, physical_memory_bytes, choices
    )


def recommended_guided_selection(
    choice: StorageDiskChoice,
    filesystem: Filesystem,
) -> GuidedStorageSelection:
    """Choose the first eligible extent and prefer an existing ESP."""

    if not choice.guided_available:
        raise ValueError("Selected disk is not available for coexistence")
    decision = choice.coexistence
    extent = decision.free_space_candidates[0].extent
    reused_esp = (
        decision.esp_candidates[0]
        if decision.esp_candidates
        else None
    )
    return GuidedStorageSelection(
        disk_stable_id=choice.disk.identity.stable_id,
        disk_size_bytes=choice.disk.identity.expected_size_bytes,
        free_extent_id=extent.extent_id,
        reused_esp_partuuid=(
            reused_esp.identity.partuuid if reused_esp is not None else ""
        ),
        filesystem=filesystem,
    )


def build_guided_storage_preview(
    workflow: StorageWorkflow,
    selection: GuidedStorageSelection,
    *,
    swap_size_mib: int | None = None,
) -> GuidedStoragePreview:
    """Build a graph-identical confirmation preview without executable code."""

    choice = workflow.disk(selection.disk_stable_id)
    disk = choice.disk
    if disk.identity.expected_size_bytes != selection.disk_size_bytes:
        raise ValueError("Selected disk size changed")
    if not choice.guided_available:
        raise ValueError("Selected disk is not available for coexistence")
    extent = next(
        (
            item.extent
            for item in choice.coexistence.free_space_candidates
            if item.extent.extent_id == selection.free_extent_id
        ),
        None,
    )
    if extent is None:
        raise ValueError("Selected free extent changed")
    reused_esp = _selected_esp(choice, selection.reused_esp_partuuid)

    swap_sizing = calculate_swap_sizing(
        workflow.physical_memory_bytes,
        extent.size_bytes,
        esp_size_mib=(0 if reused_esp is not None else 1024),
    )
    selected_swap_size_mib = (
        swap_sizing.swap_size_mib
        if swap_size_mib is None
        else swap_size_mib
    )
    validate_disk_swap_selection(selected_swap_size_mib, swap_sizing)
    plan = _preview_plan(
        selection,
        disk,
        workflow.platform,
        selected_swap_size_mib,
    )
    graph = build_guided_coexistence_storage_graph(
        plan,
        disk,
        extent,
        inventory_digest=workflow.inventory.digest,
        reused_esp=reused_esp,
    )
    plan = replace(plan, storage=replace(plan.storage, graph=graph))
    write_set = build_guided_coexistence_write_set(
        plan, workflow.inventory
    )
    return GuidedStoragePreview(
        selection=selection,
        graph=graph,
        write_set=write_set,
        disk=disk,
        extent=extent,
        reused_esp=reused_esp,
        swap_sizing=swap_sizing,
        swap_size_mib=selected_swap_size_mib,
    )


def build_guided_storage_confirmation(
    preview: GuidedStoragePreview,
) -> GuidedStorageConfirmation:
    """Reduce the typed write set into the exact user confirmation facts."""

    operations = preview.write_set.operations
    preserved = tuple(
        item.display_path
        for item in operations
        if item.action is StorageAction.PRESERVE
    )
    new_partitions = tuple(
        GuidedPartitionConfirmation(
            name=item.detail("name"),
            display_path=item.display_path,
            start_mib=int(item.detail("start_mib")),
            end_mib=int(item.detail("end_mib")),
        )
        for item in operations
        if item.action is StorageAction.CREATE_PARTITION
    )
    formats = tuple(
        GuidedFormatConfirmation(
            display_path=item.display_path,
            filesystem=item.detail("filesystem"),
        )
        for item in operations
        if item.action is StorageAction.FORMAT
    )
    return GuidedStorageConfirmation(
        preserved_paths=preserved,
        new_partitions=new_partitions,
        formats=formats,
        reused_esp_path=(
            preview.reused_esp.identity.path
            if preview.reused_esp is not None
            else ""
        ),
        writes_vendor_boot_files=any(
            item.action is StorageAction.WRITE_BOOT_FILES
            for item in operations
        ),
        writes_shared_fallback=any(
            item.action is StorageAction.WRITE_FALLBACK_BOOT_FILES
            for item in operations
        ),
        updates_nvram=any(
            item.action is StorageAction.UPDATE_NVRAM
            for item in operations
        ),
    )


def build_manual_storage_preview(
    workflow: StorageWorkflow,
    selection: ManualStorageSelection,
) -> ManualStoragePreview:
    """Validate and render one in-memory manual edit graph."""

    choice = workflow.disk(selection.disk_stable_id)
    disk = choice.disk
    if choice.is_live_media:
        raise ValueError("The Live medium cannot be edited")
    if workflow.platform.firmware.value != "uefi":
        raise ValueError("Manual partitioning currently requires UEFI")
    validate_manual_selection(disk, selection)
    swap_size_mib = next(
        (
            item.size_mib
            for item in selection.new_partitions
            if item.role is ManualPartitionRole.SWAP
        ),
        0,
    )
    plan = _manual_preview_plan(
        selection,
        disk,
        workflow.platform,
        swap_size_mib,
    )
    graph = build_manual_storage_graph(
        plan,
        disk,
        selection,
        inventory_digest=workflow.inventory.digest,
    )
    plan = replace(plan, storage=replace(plan.storage, graph=graph))
    return ManualStoragePreview(
        selection=selection,
        graph=graph,
        write_set=build_manual_storage_write_set(
            plan,
            workflow.inventory,
        ),
        disk=disk,
        available_extents=manual_available_extents(disk, selection),
    )


def build_manual_storage_confirmation(
    preview: ManualStoragePreview,
) -> ManualStorageConfirmation:
    operations = preview.write_set.operations
    return ManualStorageConfirmation(
        reinitializes_gpt=any(
            item.action is StorageAction.REPLACE_PARTITION_TABLE
            for item in operations
        ),
        preserved_paths=tuple(
            item.display_path
            for item in operations
            if item.action is StorageAction.PRESERVE
        ),
        deleted_paths=tuple(
            item.display_path
            for item in operations
            if item.action is StorageAction.DELETE_PARTITION
        ),
        resized_partitions=tuple(
            ManualResizeConfirmation(
                display_path=item.display_path,
                original_size_bytes=int(item.detail("original_size_bytes")),
                target_size_bytes=int(item.detail("target_size_bytes")),
                reclaimed_bytes=int(item.detail("reclaimed_bytes")),
            )
            for item in operations
            if item.action is StorageAction.RESIZE_PARTITION
        ),
        new_partitions=tuple(
            GuidedPartitionConfirmation(
                name=item.detail("name"),
                display_path=item.display_path,
                start_mib=int(item.detail("start_mib")),
                end_mib=int(item.detail("end_mib")),
            )
            for item in operations
            if item.action is StorageAction.CREATE_PARTITION
        ),
        formats=tuple(
            GuidedFormatConfirmation(
                display_path=item.display_path,
                filesystem=item.detail("filesystem"),
            )
            for item in operations
            if item.action is StorageAction.FORMAT
        ),
        reused_esp_path=(
            next(
                (
                    item.identity.path
                    for item in preview.disk.partitions
                    if item.identity.partuuid
                    == preview.selection.reused_esp_partuuid
                ),
                "",
            )
        ),
        writes_vendor_boot_files=any(
            item.action is StorageAction.WRITE_BOOT_FILES
            for item in operations
        ),
        writes_shared_fallback=any(
            item.action is StorageAction.WRITE_FALLBACK_BOOT_FILES
            for item in operations
        ),
        updates_nvram=any(
            item.action is StorageAction.UPDATE_NVRAM
            for item in operations
        ),
    )


def _selected_esp(
    choice: StorageDiskChoice,
    partuuid: str,
) -> PartitionInventory | None:
    if not partuuid:
        return None
    selected = next(
        (
            item
            for item in choice.coexistence.esp_candidates
            if item.identity.partuuid == partuuid
        ),
        None,
    )
    if selected is None:
        raise ValueError("Selected EFI System Partition changed")
    return selected


def _preview_plan(
    selection: GuidedStorageSelection,
    disk: DiskInventory,
    platform: PlatformProbe,
    swap_size_mib: int,
) -> InstallPlan:
    """Create a secret-free draft used only by graph and write-set builders."""

    return InstallPlan(
        schema_version=SCHEMA_VERSION,
        source=SourceSpec(),
        storage=StorageSpec(
            mode=InstallMode.GUIDED_COEXISTENCE,
            disk=disk.identity,
            filesystem=selection.filesystem,
            swap_size_mib=swap_size_mib,
        ),
        platform=PlatformSpec(
            architecture=platform.architecture,
            firmware=platform.firmware,
            secure_boot=platform.secure_boot,
        ),
        identity=IdentitySpec(
            hostname="preview",
            username="preview",
            full_name="Storage Preview",
            authentication=AuthenticationMode.PASSWORDLESS_SHARED,
        ),
        access=AccessSpec(
            sudo_without_password=True,
            automatic_login=True,
        ),
        regional=RegionalSpec(
            locale=DEFAULT_LOCALE,
            timezone=DEFAULT_TIMEZONE,
            keyboard=KeyboardSpec(DEFAULT_KEYBOARD),
        ),
        boot=BootSpec(
            install_fallback_path=False,
            mok_password_policy=(
                MokPasswordPolicy.ANDUINOS_DEFAULT
                if platform.secure_boot is SecureBoot.ENABLED
                else MokPasswordPolicy.NOT_APPLICABLE
            ),
        ),
    )


def _manual_preview_plan(
    selection: ManualStorageSelection,
    disk: DiskInventory,
    platform: PlatformProbe,
    swap_size_mib: int,
) -> InstallPlan:
    return replace(
        _preview_plan(
            GuidedStorageSelection(
                disk_stable_id=selection.disk_stable_id,
                disk_size_bytes=selection.disk_size_bytes,
                free_extent_id="manual-preview",
                reused_esp_partuuid=selection.reused_esp_partuuid,
                filesystem=selection.filesystem,
            ),
            disk,
            platform,
            swap_size_mib,
        ),
        storage=StorageSpec(
            mode=InstallMode.MANUAL,
            disk=disk.identity,
            filesystem=selection.filesystem,
            swap_size_mib=swap_size_mib,
        ),
    )
