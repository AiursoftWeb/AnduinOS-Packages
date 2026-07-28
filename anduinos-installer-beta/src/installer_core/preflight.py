"""Release-gate checks repeated by the privileged process."""

from __future__ import annotations

from collections.abc import Callable

from .command import CommandRunner
from .model import DiskIdentity, InstallPlan, PlatformSpec
from .probe import PlatformProbe, probe_disks, probe_platform
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

