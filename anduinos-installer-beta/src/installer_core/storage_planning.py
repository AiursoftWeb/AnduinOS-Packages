"""Compose the current erase-disk layout, commands and typed write set."""

from __future__ import annotations

from dataclasses import dataclass

from .btrfs import BTRFS_SUBVOLUMES
from .boot_commands import (
    GuidedBootCommandPlan,
    build_guided_coexistence_boot_commands,
)
from .esp import (
    GUIDED_ESP_MINIMUM_FREE_BYTES,
    EspReuseInspection,
    NvramInspection,
)
from .layout import PartitionLayout, build_erase_disk_layout
from .model import Filesystem, InstallMode, InstallPlan
from .storage_commands import (
    StorageCommandPlan,
    build_guided_coexistence_storage_commands,
    build_storage_commands,
    partition_path,
)
from .storage_graph import BlockReferenceKind, StorageGraphAction
from .storage_graph_planning import validate_guided_coexistence_graph
from .storage_inventory import (
    DiskInventory,
    PartitionIdentity,
    PartitionInventory,
    StorageInventory,
)
from .storage_write_set import (
    StorageAction,
    StorageWriteSet,
    build_erase_disk_write_set,
    build_guided_coexistence_write_set,
)
from .validation import validate_plan


@dataclass(frozen=True)
class EraseDiskExecutionPlan:
    """One immutable source for release-one storage execution and display."""

    layout: PartitionLayout
    commands: StorageCommandPlan
    write_set: StorageWriteSet


@dataclass(frozen=True)
class GuidedCoexistenceExecutionPlan:
    """Frozen free-space writes and shared-ESP boot policy."""

    commands: StorageCommandPlan
    boot_commands: GuidedBootCommandPlan
    write_set: StorageWriteSet
    esp_partition_number: int
    reuses_esp: bool


def build_erase_disk_execution_plan(
    plan: InstallPlan,
) -> EraseDiskExecutionPlan:
    layout = build_erase_disk_layout(plan)
    commands = build_storage_commands(plan, layout)
    write_set = build_erase_disk_write_set(plan)
    _verify_parity(plan, layout, commands, write_set)
    return EraseDiskExecutionPlan(layout, commands, write_set)


def build_guided_coexistence_execution_plan(
    plan: InstallPlan,
    inventory: StorageInventory,
    *,
    esp_inspection: EspReuseInspection | None,
    nvram_inspection: NvramInspection,
    target: str = "/target",
) -> GuidedCoexistenceExecutionPlan:
    """Compile only after current topology and boot prerequisites pass."""

    if plan.storage.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise ValueError("Plan is not guided coexistence")
    validate_plan(plan, allow_guided_compilation=True)
    disk = validate_guided_coexistence_graph(plan, inventory)
    graph = plan.storage.graph
    assert graph is not None
    esp, reused = _guided_esp_partition(plan, disk)
    _validate_guided_prerequisites(
        esp,
        reused=reused,
        esp_inspection=esp_inspection,
        nvram_inspection=nvram_inspection,
    )
    commands = build_guided_coexistence_storage_commands(plan, inventory)
    write_set = build_guided_coexistence_write_set(plan, inventory)
    boot_commands = build_guided_coexistence_boot_commands(
        plan,
        target,
        disk_path=disk.identity.path,
        esp_partition_number=esp.identity.number,
    )
    execution = GuidedCoexistenceExecutionPlan(
        commands=commands,
        boot_commands=boot_commands,
        write_set=write_set,
        esp_partition_number=esp.identity.number,
        reuses_esp=reused,
    )
    _verify_guided_parity(plan, execution)
    return execution


def resolve_guided_esp_partition(
    plan: InstallPlan,
    inventory: StorageInventory,
) -> tuple[PartitionInventory, bool]:
    """Resolve the selected ESP from a freshly validated guided graph."""

    if plan.storage.mode is not InstallMode.GUIDED_COEXISTENCE:
        raise ValueError("Plan is not guided coexistence")
    validate_plan(plan, allow_guided_compilation=True)
    disk = validate_guided_coexistence_graph(plan, inventory)
    return _guided_esp_partition(plan, disk)


def _verify_parity(
    plan: InstallPlan,
    layout: PartitionLayout,
    commands: StorageCommandPlan,
    write_set: StorageWriteSet,
) -> None:
    """Fail closed if execution and user-visible declarations drift."""

    layout_names = {item.name for item in layout.partitions}
    if set(commands.devices) != layout_names:
        raise RuntimeError("Storage command devices do not match the layout")

    table_operations = [
        item
        for item in write_set.operations
        if item.action is StorageAction.REPLACE_PARTITION_TABLE
    ]
    if (
        len(table_operations) != 1
        or table_operations[0].detail("table") != layout.table
    ):
        raise RuntimeError("Storage write set does not match the partition table")

    creates = {
        item.detail("name"): item.display_path
        for item in write_set.operations
        if item.action is StorageAction.CREATE_PARTITION
    }
    if creates != commands.devices:
        raise RuntimeError("Storage write set partition devices drifted")

    expected_formats = {
        commands.devices["efi-system"]: "vfat",
        commands.devices["root"]: plan.storage.filesystem.value,
    }
    if "swap" in commands.devices:
        expected_formats[commands.devices["swap"]] = "swap"
    command_formats = _command_formats(commands)
    if command_formats != expected_formats:
        raise RuntimeError("Storage format commands do not match the layout")
    declared_formats = {
        item.display_path: item.detail("filesystem")
        for item in write_set.operations
        if item.action is StorageAction.FORMAT
    }
    if declared_formats != command_formats:
        raise RuntimeError("Storage write set formats drifted")

    actual_subvolumes = tuple(
        item.detail("name")
        for item in write_set.operations
        if item.action is StorageAction.CREATE_SUBVOLUME
    )
    expected_subvolumes = (
        tuple(item.name for item in BTRFS_SUBVOLUMES)
        if plan.storage.filesystem is Filesystem.BTRFS
        else ()
    )
    if actual_subvolumes != expected_subvolumes:
        raise RuntimeError("Storage write set subvolumes drifted")

    graph = plan.storage.graph
    if graph is None:
        raise RuntimeError("Storage graph is missing")
    if graph.partition_table != layout.table:
        raise RuntimeError("Storage graph partition table drifted")
    graph_operations = tuple(
        (item.action.value, item.target_id) for item in graph.operations
    )
    declared_operations = tuple(
        (item.action.value, item.target_id) for item in write_set.operations
    )
    if graph_operations != declared_operations:
        raise RuntimeError("Storage graph operations drifted")


def _command_formats(commands: StorageCommandPlan) -> dict[str, str]:
    result: dict[str, str] = {}
    command_types = {
        "mkfs.vfat": "vfat",
        "mkswap": "swap",
        "mkfs.btrfs": "btrfs",
        "mkfs.ext4": "ext4",
    }
    for command in commands.format:
        filesystem = command_types.get(command[0])
        if filesystem is None or len(command) < 2:
            raise RuntimeError("Storage command plan has an unknown formatter")
        device = command[-1]
        if device in result:
            raise RuntimeError("Storage command plan formats a device twice")
        result[device] = filesystem
    return result


def _guided_esp_partition(
    plan: InstallPlan,
    disk: DiskInventory,
) -> tuple[PartitionInventory, bool]:
    graph = plan.storage.graph
    assert graph is not None
    esp_id = graph.boot_targets[0].efi_filesystem_id
    references = {
        item.reference_id: item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.PARTITION
    }
    reference = references.get(esp_id)
    if reference is not None:
        partition = next(
            item
            for item in disk.partitions
            if item.identity.partuuid == reference.stable_id
        )
        return partition, True
    declaration = next(
        item for item in graph.partitions if item.partition_id == esp_id
    )
    return (
        PartitionInventory(
            identity=_new_partition_identity(
                disk.identity.path,
                declaration.number,
                declaration.start_mib,
                declaration.end_mib,
            ),
            parent_disk_id=disk.identity.stable_id,
        ),
        False,
    )


def _new_partition_identity(
    disk_path: str,
    number: int,
    start_mib: int,
    end_mib: int | None,
) -> PartitionIdentity:
    if end_mib is None:
        raise RuntimeError("Coexistence ESP has no bounded end")
    return PartitionIdentity(
        path=partition_path(disk_path, number),
        number=number,
        partuuid="",
        start_bytes=start_mib * 1024 * 1024,
        size_bytes=(end_mib - start_mib) * 1024 * 1024,
    )


def _validate_guided_prerequisites(
    esp: PartitionInventory,
    *,
    reused: bool,
    esp_inspection: EspReuseInspection | None,
    nvram_inspection: NvramInspection,
) -> None:
    if not nvram_inspection.available:
        reason = nvram_inspection.reason or "UEFI variables are unavailable"
        raise RuntimeError(
            "Cannot safely create the AnduinOS firmware boot entry: " + reason
        )
    if not reused:
        if esp_inspection is not None:
            raise RuntimeError("An ESP inspection was supplied for a new ESP")
        return
    if esp_inspection is None:
        raise RuntimeError("A fresh shared ESP inspection is required")
    if (
        esp_inspection.partuuid != esp.identity.partuuid
        or esp_inspection.filesystem_uuid != esp.filesystem_uuid
    ):
        raise RuntimeError("Shared ESP identity changed after inspection")
    if not esp_inspection.healthy:
        reason = esp_inspection.reason or "FAT consistency check failed"
        raise RuntimeError("Shared ESP is not healthy: " + reason)
    if esp_inspection.free_bytes < GUIDED_ESP_MINIMUM_FREE_BYTES:
        required_mib = GUIDED_ESP_MINIMUM_FREE_BYTES // (1024 * 1024)
        raise RuntimeError(
            f"Shared ESP requires at least {required_mib} MiB free space"
        )


def _verify_guided_parity(
    plan: InstallPlan,
    execution: GuidedCoexistenceExecutionPlan,
) -> None:
    graph = plan.storage.graph
    assert graph is not None
    graph_operations = tuple(
        (item.action.value, item.target_id) for item in graph.operations
    )
    write_operations = tuple(
        (item.action.value, item.target_id)
        for item in execution.write_set.operations
    )
    if graph_operations != write_operations:
        raise RuntimeError("Coexistence graph and write set drifted")

    forbidden = {"mklabel", "mktable", "rm", "resizepart"}
    if any(
        forbidden.intersection(command)
        for command in execution.commands.partition
    ):
        raise RuntimeError("Coexistence commands contain a forbidden table edit")
    creates = tuple(
        command
        for command in execution.commands.partition
        if "mkpart" in command
    )
    if len(creates) != len(graph.partitions):
        raise RuntimeError("Coexistence partition commands drifted")
    for declaration, command in zip(graph.partitions, creates, strict=True):
        if command[-2:] != (
            f"{declaration.start_mib}MiB",
            f"{declaration.end_mib}MiB",
        ):
            raise RuntimeError("Coexistence partition geometry drifted")

    expected_formats = {
        execution.commands.devices[
            next(
                part.name
                for part in graph.partitions
                if part.partition_id == item.target_id
            )
        ]: next(
            filesystem.filesystem.value
            for filesystem in graph.filesystems
            if filesystem.block_id == item.target_id
        )
        for item in graph.operations
        if item.action is StorageGraphAction.FORMAT
    }
    if _command_formats(execution.commands) != expected_formats:
        raise RuntimeError("Coexistence format commands drifted")

    boot = execution.boot_commands
    if (
        "--no-extra-removable" not in boot.install
        or "--no-nvram" not in boot.install
        or any("i386-pc" in item for item in boot.install)
    ):
        raise RuntimeError("Coexistence boot command violates shared ESP policy")
    nvram_writes = tuple(
        item
        for item in execution.write_set.operations
        if item.action is StorageAction.UPDATE_NVRAM
    )
    if len(nvram_writes) != 1 or (
        nvram_writes[0].detail("loader") != boot.loader_path
    ):
        raise RuntimeError("Coexistence NVRAM command drifted")
