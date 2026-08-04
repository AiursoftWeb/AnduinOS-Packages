"""Pure state model for the gated GTK storage workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace

from languages import DEFAULT_KEYBOARD, DEFAULT_LOCALE, DEFAULT_TIMEZONE

from .coexistence import (
    CoexistenceStatus,
    CoexistenceDecision,
    analyze_guided_coexistence,
)
from .model import (
    SCHEMA_VERSION,
    AuthenticationMode,
    BootSpec,
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
from .probe import PlatformProbe
from .storage_graph import StorageGraph
from .storage_graph_planning import build_guided_coexistence_storage_graph
from .storage_inventory import (
    DiskInventory,
    FreeExtent,
    PartitionInventory,
    StorageInventory,
)
from .swap_policy import (
    SwapSizing,
    calculate_swap_sizing,
    probe_physical_memory_bytes,
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
    plan = _preview_plan(
        selection,
        disk,
        workflow.platform,
        swap_sizing.swap_size_mib,
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
            sudo_without_password=True,
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
