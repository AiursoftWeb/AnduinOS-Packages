"""Release-gate checks repeated by the privileged process."""

from __future__ import annotations

import json
from collections.abc import Callable

from .command import CommandRunner
from .model import DiskIdentity, InstallPlan, PlatformSpec
from .probe import PlatformProbe, probe_disks, probe_platform
from .storage_commands import partition_path
from .validation import validate_plan


class PreflightError(RuntimeError):
    pass


def verify_execution_environment(
    plan: InstallPlan,
    runner: CommandRunner,
    *,
    platform_probe: Callable[[], PlatformProbe] = probe_platform,
    disk_probe: Callable[[], tuple[DiskIdentity, ...]] = probe_disks,
) -> None:
    """Reject stale or substituted hardware before any destructive command."""
    verify_platform_environment(
        plan,
        runner,
        platform_probe=platform_probe,
    )
    verify_target_disk_environment(
        plan,
        runner,
        disk_probe=disk_probe,
    )


def verify_platform_environment(
    plan: InstallPlan,
    runner: CommandRunner,
    *,
    platform_probe: Callable[[], PlatformProbe] = probe_platform,
) -> None:
    validate_plan(plan)
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
    disk_probe: Callable[[], tuple[DiskIdentity, ...]] = probe_disks,
) -> None:
    validate_plan(plan)
    runner.require_root()

    selected = next(
        (disk for disk in disk_probe() if disk.path == plan.storage.disk.path),
        None,
    )
    if selected is None:
        raise PreflightError("Selected target disk is no longer available")
    expected = plan.storage.disk
    if selected.stable_id != expected.stable_id:
        raise PreflightError("Target disk stable identity changed")
    if selected.expected_size_bytes != expected.expected_size_bytes:
        raise PreflightError("Target disk size changed")
    _reject_active_target_disk(runner, selected.path)


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
