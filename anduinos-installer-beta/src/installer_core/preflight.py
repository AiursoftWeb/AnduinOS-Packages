"""Release-gate checks repeated by the privileged process."""

from __future__ import annotations

import json
from collections.abc import Callable

from .command import CommandRunner
from .model import InstallPlan, PlatformSpec
from .probe import PlatformProbe, probe_platform
from .storage_commands import partition_path
from .storage_graph_planning import resolve_storage_graph
from .storage_inventory import StorageInventory, probe_storage_inventory
from .validation import (
    ExecutionPolicy,
    validate_plan_for_execution,
)


class PreflightError(RuntimeError):
    pass


def verify_execution_environment(
    plan: InstallPlan,
    runner: CommandRunner,
    *,
    platform_probe: Callable[[], PlatformProbe] = probe_platform,
    inventory_probe: Callable[[], StorageInventory] = probe_storage_inventory,
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
    execution_policy: ExecutionPolicy = ExecutionPolicy.RELEASE,
) -> InstallPlan:
    validate_plan_for_execution(plan, execution_policy)
    runner.require_root()

    try:
        resolved_plan = resolve_storage_graph(plan, inventory_probe())
    except ValueError as error:
        raise PreflightError(str(error)) from error
    _reject_active_target_disk(runner, resolved_plan.storage.disk.path)
    return resolved_plan


def _reject_active_target_disk(runner: CommandRunner, disk: str) -> None:
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

    for device in _walk_block_devices(roots):
        path = str(device.get("path") or disk)
        mountpoints = tuple(
            str(item)
            for item in (device.get("mountpoints") or ())
            if item
        )
        retry_swap = partition_path(disk, 3)
        if (
            mountpoints == ("[SWAP]",)
            and path == retry_swap
            and str(device.get("type") or "") == "part"
        ):
            # The whole-disk layout always owns partition 3 as swap.  A failed
            # earlier attempt may leave it active; PrepareStorageStep safely
            # disables this exact partition before changing the table.
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


def _walk_block_devices(devices):
    for device in devices:
        yield device
        yield from _walk_block_devices(device.get("children") or ())
