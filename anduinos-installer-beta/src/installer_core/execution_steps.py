"""Non-storage execution steps for the first runnable backend milestone."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Callable

from .command import CommandRunner
from .preflight import (
    verify_platform_environment,
    verify_target_disk_environment,
)
from .probe import probe_platform
from .model import Architecture, Firmware, InstallMode, SecureBoot
from .steps import FailurePolicy, InstallContext
from .storage_inventory import probe_storage_inventory
from .storage_steps import deactivate_target_swap
from .swap_policy import probe_physical_memory_bytes


@dataclass
class DetectBootEnvironmentStep:
    runner: CommandRunner
    platform_probe: object = probe_platform
    id: str = "detect-boot-environment"
    title: str = "Detect firmware and Secure Boot"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        verify_platform_environment(
            context.plan,
            self.runner,
            platform_probe=self.platform_probe,
            execution_policy=context.execution_policy,
        )

    def execute(self, context: InstallContext) -> None:
        platform = context.plan.platform
        firmware = (
            "UEFI"
            if platform.firmware is Firmware.UEFI
            else "Legacy BIOS"
        )
        secure_boot = {
            SecureBoot.ENABLED: "enabled",
            SecureBoot.DISABLED: "disabled",
            SecureBoot.UNSUPPORTED: "unsupported by firmware",
            SecureBoot.NOT_APPLICABLE: "not applicable in Legacy BIOS mode",
        }[platform.secure_boot]
        context.log(f"Architecture: {platform.architecture.value}")
        context.log(f"Firmware mode: {firmware}")
        context.log(f"Secure Boot: {secure_boot}")
        if platform.firmware is Firmware.BIOS:
            context.log(
                "Firmware detection: /sys/firmware/efi is absent"
            )
        else:
            context.log(
                "Firmware detection: /sys/firmware/efi is present"
            )
        context.log(
            "Legacy BIOS GRUB: "
            + (
                "enabled"
                if platform.architecture is Architecture.AMD64
                else "not supported on arm64"
            )
        )
        guided = platform.firmware is Firmware.UEFI and (
            context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
        )
        context.log(
            "UEFI fallback bootloader: "
            + (
                "preserved; no fallback write"
                if guided
                else "enabled on the selected disk"
            )
        )
        context.log(
            "UEFI Boot#### entries: "
            + (
                "create and verify AnduinOS only"
                if guided
                else "will not be modified"
            )
        )

    def verify(self, context: InstallContext) -> None:
        return None

    def cleanup(self, context: InstallContext) -> None:
        return None


@dataclass
class VerifyTargetDiskStep:
    runner: CommandRunner
    inventory_probe: object = probe_storage_inventory
    physical_memory_probe: object = probe_physical_memory_bytes
    id: str = "verify-target-disk"
    title: str = "Verify target disk isolation"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        context.plan = verify_target_disk_environment(
            context.plan,
            self.runner,
            inventory_probe=self.inventory_probe,
            physical_memory_probe=self.physical_memory_probe,
            execution_policy=context.execution_policy,
        )

    def execute(self, context: InstallContext) -> None:
        disk = context.plan.storage.disk
        context.log(f"Selected target disk: {disk.path}")
        context.log(f"Target disk identity: {disk.stable_id}")
        context.log(f"Target disk size: {disk.expected_size_bytes} bytes")
        if context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE:
            context.log(
                "Only the selected unallocated extent will receive new "
                "partitions"
            )
            context.log(
                "Every pre-existing partition on the selected disk is "
                "preserve-marked"
            )
        else:
            context.log(
                "Only the selected disk will be partitioned and formatted"
            )
            context.log(
                "Other disks and their EFI System Partitions will not be "
                "modified"
            )
        if context.plan.platform.firmware is Firmware.UEFI:
            context.log(
                "UEFI Windows systems on other disks will be checked "
                "read-only and added to the AnduinOS GRUB menu"
            )

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
    wait: Callable[[float], None] = sleep
    id: str = "unmount-target"
    title: str = "Unmount target filesystems"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 1
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        self.runner.require_commands(("sync", "umount", "swapon", "swapoff"))

    def execute(self, context: InstallContext) -> None:
        target = _target(context)
        self.runner.run(("sync",), timeout=300)
        self.wait(3)
        self.runner.run(("sync",), timeout=300)
        if context.values.get("target_efi_mounted"):
            self.runner.run(("umount", str(target / "boot/efi")), timeout=30)
            context.values["target_efi_mounted"] = False
        for path in reversed(context.values.get("target_btrfs_mounts", [])):
            self.runner.run(("umount", str(path)), timeout=30)
        context.values["target_btrfs_mounts"] = []
        if context.values.get("target_root_mounted"):
            self.runner.run(("umount", str(target)), timeout=30)
            context.values["target_root_mounted"] = False
        deactivate_target_swap(context, self.runner)

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
