"""Non-storage execution steps for the first runnable backend milestone."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .command import CommandRunner
from .preflight import verify_execution_environment
from .probe import probe_disks, probe_platform
from .steps import FailurePolicy, InstallContext


@dataclass
class VerifyEnvironmentStep:
    runner: CommandRunner
    platform_probe: object = probe_platform
    disk_probe: object = probe_disks
    id: str = "verify-environment"
    title: str = "Verify platform and target disk"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        verify_execution_environment(
            context.plan,
            self.runner,
            platform_probe=self.platform_probe,
            disk_probe=self.disk_probe,
        )

    def execute(self, context: InstallContext) -> None:
        return None

    def verify(self, context: InstallContext) -> None:
        return None

    def cleanup(self, context: InstallContext) -> None:
        return None


@dataclass
class CopySystemStep:
    runner: CommandRunner
    id: str = "copy-system"
    title: str = "Copy system image"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 60
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        self.runner.require_commands(("unsquashfs",))
        source = Path(context.plan.source.image_path)
        if not source.is_file():
            raise RuntimeError(f"System image not found: {source}")

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        self.runner.run(
            (
                "unsquashfs",
                "-force",
                "-dest",
                str(target),
                context.plan.source.image_path,
            ),
            timeout=3600,
        )

    def verify(self, context: InstallContext) -> None:
        target = _target(context)
        required = (target / "etc/os-release", target / "usr", target / "var")
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError(
                "Copied system is incomplete: " + ", ".join(missing)
            )

    def cleanup(self, context: InstallContext) -> None:
        return None


@dataclass
class UnmountTargetStep:
    runner: CommandRunner
    id: str = "unmount-target"
    title: str = "Unmount target filesystems"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        self.runner.require_commands(("umount",))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        if context.values.get("target_efi_mounted"):
            self.runner.run(("umount", str(target / "boot/efi")), timeout=30)
            context.values["target_efi_mounted"] = False
        for path in reversed(context.values.get("target_btrfs_mounts", [])):
            self.runner.run(("umount", str(path)), timeout=30)
        context.values["target_btrfs_mounts"] = []
        if context.values.get("target_root_mounted"):
            self.runner.run(("umount", str(target)), timeout=30)
            context.values["target_root_mounted"] = False

    def verify(self, context: InstallContext) -> None:
        if context.values.get("target_efi_mounted") or context.values.get(
            "target_root_mounted"
        ) or context.values.get("target_btrfs_mounts"):
            raise RuntimeError("Target mount state was not cleared")

    def cleanup(self, context: InstallContext) -> None:
        return None


def _target(context: InstallContext) -> Path:
    target = context.values.get("target")
    if not isinstance(target, Path):
        raise RuntimeError("Target filesystem is not mounted")
    return target
