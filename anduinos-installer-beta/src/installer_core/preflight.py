"""Release-gate checks repeated by the privileged process."""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .model import Architecture, InstallMode, InstallPlan, PlatformSpec
from .probe import PlatformProbe, probe_platform
from .storage_commands import partition_path
from .storage_graph import BlockReferenceKind
from .storage_graph_planning import resolve_storage_graph
from .storage_inventory import StorageInventory, probe_storage_inventory
from .swap_policy import (
    SwapSizingError,
    calculate_swap_sizing,
    probe_physical_memory_bytes,
    validate_disk_swap_selection,
)
from .validation import (
    ExecutionPolicy,
    validate_plan_for_execution,
)


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class NamespaceMount:
    device: str
    mountpoint: str
    pid: int


NamespaceMountProbe = Callable[[tuple[str, ...]], NamespaceMount | None]


def verify_execution_environment(
    plan: InstallPlan,
    runner: CommandRunner,
    *,
    platform_probe: Callable[[], PlatformProbe] = probe_platform,
    inventory_probe: Callable[[], StorageInventory] = probe_storage_inventory,
    namespace_mount_probe: NamespaceMountProbe | None = None,
    physical_memory_probe: Callable[[], int] | None = None,
    execution_policy: ExecutionPolicy = ExecutionPolicy.RELEASE,
) -> InstallPlan:
    """Reject stale or substituted hardware before any destructive command."""
    verify_platform_environment(
        plan,
        runner,
        platform_probe=platform_probe,
        execution_policy=execution_policy,
    )
    return verify_target_disk_environment(
        plan,
        runner,
        inventory_probe=inventory_probe,
        namespace_mount_probe=namespace_mount_probe,
        physical_memory_probe=physical_memory_probe,
        execution_policy=execution_policy,
    )


def verify_platform_environment(
    plan: InstallPlan,
    runner: CommandRunner,
    *,
    platform_probe: Callable[[], PlatformProbe] = probe_platform,
    execution_policy: ExecutionPolicy = ExecutionPolicy.RELEASE,
) -> None:
    validate_plan_for_execution(plan, execution_policy)
    runner.require_root()

    actual_platform = platform_probe()
    expected_platform = PlatformSpec(
        actual_platform.architecture,
        actual_platform.firmware,
        actual_platform.secure_boot,
    )
    if expected_platform != plan.platform:
        raise PreflightError(
            f"Platform changed since planning: expected {plan.platform}, "
            f"found {expected_platform}"
        )


def verify_target_disk_environment(
    plan: InstallPlan,
    runner: CommandRunner,
    *,
    inventory_probe: Callable[[], StorageInventory] = probe_storage_inventory,
    namespace_mount_probe: NamespaceMountProbe | None = None,
    physical_memory_probe: Callable[[], int] | None = None,
    execution_policy: ExecutionPolicy = ExecutionPolicy.RELEASE,
) -> InstallPlan:
    validate_plan_for_execution(plan, execution_policy)
    runner.require_root()

    try:
        resolved_plan = resolve_storage_graph(plan, inventory_probe())
    except ValueError as error:
        raise PreflightError(str(error)) from error
    try:
        swap_sizing = calculate_swap_sizing(
            (physical_memory_probe or probe_physical_memory_bytes)(),
            _installation_space_bytes(resolved_plan),
            esp_size_mib=_installation_esp_size_mib(resolved_plan),
        )
        validate_disk_swap_selection(
            resolved_plan.storage.swap_size_mib,
            swap_sizing,
        )
    except (RuntimeError, SwapSizingError, ValueError) as error:
        raise PreflightError(
            f"Could not validate disk swap size: {error}"
        ) from error
    _reject_active_target_disk(
        runner,
        resolved_plan,
        namespace_mount_probe=namespace_mount_probe,
    )
    return resolved_plan


def _installation_space_bytes(plan: InstallPlan) -> int:
    if plan.storage.mode is InstallMode.ERASE_DISK:
        return plan.storage.disk.expected_size_bytes
    graph = plan.storage.graph
    if graph is None:
        raise ValueError("Guided storage graph is missing")
    extents = tuple(
        item
        for item in graph.block_references
        if item.kind is BlockReferenceKind.FREE_EXTENT
    )
    if len(extents) != 1:
        raise ValueError(
            "Guided storage graph must bind exactly one free extent"
        )
    return extents[0].expected_size_bytes


def _installation_esp_size_mib(plan: InstallPlan) -> int:
    if plan.storage.mode is InstallMode.ERASE_DISK:
        return plan.storage.esp_size_mib
    graph = plan.storage.graph
    if graph is None:
        raise ValueError("Guided storage graph is missing")
    return (
        plan.storage.esp_size_mib
        if any(item.name == "efi-system" for item in graph.partitions)
        else 0
    )


def _reject_active_target_disk(
    runner: CommandRunner,
    plan: InstallPlan,
    *,
    namespace_mount_probe: NamespaceMountProbe | None = None,
) -> None:
    disk = plan.storage.disk.path
    runner.require_commands(("lsblk",))
    result = runner.run(
        (
            "lsblk",
            "--json",
            "--paths",
            "--output",
            "PATH,TYPE,MOUNTPOINTS",
            disk,
        ),
        check=False,
        timeout=10,
        log_output=False,
    )
    if result.returncode != 0:
        raise PreflightError(
            result.stderr.strip()
            or f"Could not inspect target disk usage: {disk}"
        )
    try:
        roots = json.loads(result.stdout)["blockdevices"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise PreflightError(
            "lsblk returned invalid target usage data"
        ) from error

    graph = plan.storage.graph
    assert graph is not None
    allowed_retry_swaps = {
        partition_path(disk, item.number)
        for item in graph.partitions
        if item.name == "swap"
    }
    if plan.storage.mode is InstallMode.ERASE_DISK:
        allowed_retry_swaps.add(
            partition_path(
                disk,
                3
                if plan.platform.architecture is Architecture.AMD64
                else 2,
            )
        )

    devices = tuple(_walk_block_devices(roots))
    for device in devices:
        path = str(device.get("path") or disk)
        mountpoints = tuple(
            str(item)
            for item in (device.get("mountpoints") or ())
            if item
        )
        if (
            mountpoints == ("[SWAP]",)
            and path in allowed_retry_swaps
            and str(device.get("type") or "") == "part"
        ):
            # A failed attempt can leave either the currently planned swap or
            # the former erase-disk swap active. PrepareStorageStep disables
            # only that exact device before changing the partition table.
            continue
        if mountpoints:
            raise PreflightError(
                f"Target disk is in use: {path} is mounted at "
                + ", ".join(mountpoints)
            )
        device_type = str(device.get("type") or "")
        if device_type not in {"disk", "part"}:
            raise PreflightError(
                f"Target disk is in use by {device_type or 'an unknown mapping'}: "
                f"{path}"
            )

    device_paths = tuple(
        str(device.get("path"))
        for device in devices
        if device.get("path")
    )
    probe = namespace_mount_probe or probe_cross_namespace_target_mount
    leaked_mount = probe(device_paths)
    if leaked_mount is not None:
        raise PreflightError(
            "Target disk is still mounted in another process mount namespace: "
            f"{leaked_mount.device} at {leaked_mount.mountpoint} "
            f"(PID {leaked_mount.pid}). Restart the Live environment before "
            "retrying installation."
        )


def probe_cross_namespace_target_mount(
    device_paths: tuple[str, ...],
) -> NamespaceMount | None:
    """Find a target-device mount hidden from the executor's namespace."""

    target_devices: dict[tuple[int, int], str] = {}
    for path in device_paths:
        try:
            device_stat = os.stat(path)
        except OSError:
            continue
        if stat.S_ISBLK(device_stat.st_mode):
            target_devices[
                (os.major(device_stat.st_rdev), os.minor(device_stat.st_rdev))
            ] = path
    if not target_devices:
        return None

    try:
        current_namespace = _namespace_identity(Path("/proc/self/ns/mnt"))
    except OSError as error:
        raise PreflightError(
            f"Could not inspect the installer mount namespace: {error}"
        ) from error

    seen_namespaces = {current_namespace}
    for mountinfo in sorted(Path("/proc").glob("[0-9]*/mountinfo")):
        try:
            pid = int(mountinfo.parts[-2])
            namespace = _namespace_identity(mountinfo.parent / "ns/mnt")
            if namespace in seen_namespaces:
                continue
            contents = mountinfo.read_text(errors="replace")
        except (OSError, ValueError):
            # Processes can disappear while /proc is being inspected.
            continue
        seen_namespaces.add(namespace)
        match = _find_target_mount(contents, target_devices, pid)
        if match is not None:
            return match
    return None


def _find_target_mount(
    contents: str,
    target_devices: dict[tuple[int, int], str],
    pid: int,
) -> NamespaceMount | None:
    for line in contents.splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 5 or len(right_fields) < 2:
            continue
        mountpoint = _unescape_mountinfo_field(left_fields[4])
        device = target_devices.get(_parse_device_number(left_fields[2]))
        if device is None:
            source = _unescape_mountinfo_field(right_fields[1])
            try:
                source_stat = os.stat(source)
            except OSError:
                continue
            if stat.S_ISBLK(source_stat.st_mode):
                device = target_devices.get(
                    (
                        os.major(source_stat.st_rdev),
                        os.minor(source_stat.st_rdev),
                    )
                )
        if device is not None:
            return NamespaceMount(device, mountpoint, pid)
    return None


def _namespace_identity(path: Path) -> tuple[int, int]:
    namespace_stat = path.stat()
    return namespace_stat.st_dev, namespace_stat.st_ino


def _parse_device_number(value: str) -> tuple[int, int] | None:
    try:
        major, minor = value.split(":", 1)
        return int(major), int(minor)
    except (TypeError, ValueError):
        return None


_MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape_mountinfo_field(value: str) -> str:
    return _MOUNTINFO_ESCAPE.sub(
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def _walk_block_devices(devices):
    for device in devices:
        yield device
        yield from _walk_block_devices(device.get("children") or ())
