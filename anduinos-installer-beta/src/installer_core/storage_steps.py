"""Fatal storage steps owned by the trusted executor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .btrfs import BTRFS_SUBVOLUMES
from .command import CommandRunner
from .layout import build_erase_disk_layout
from .model import Filesystem
from .steps import FailurePolicy, InstallContext
from .storage_commands import build_storage_commands, partition_path
from .validation import validate_plan


@dataclass
class PrepareStorageStep:
    runner: CommandRunner
    id: str = "prepare-storage"
    title: str = "Partition and format target disk"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 10
    destructive: bool = True

    def preflight(self, context: InstallContext) -> None:
        validate_plan(context.plan)
        commands = [
            "parted",
            "partprobe",
            "udevadm",
            "mkfs.vfat",
            "mkswap",
            "swapon",
            "swapoff",
        ]
        commands.append(
            "mkfs.btrfs"
            if context.plan.storage.filesystem is Filesystem.BTRFS
            else "mkfs.ext4"
        )
        self.runner.require_commands(commands)

    def execute(self, context: InstallContext) -> None:
        layout = build_erase_disk_layout(context.plan)
        commands = build_storage_commands(context.plan, layout)
        context.values["layout"] = layout
        context.values["partition_devices"] = commands.devices
        # A previous failed attempt can leave the newly-created swap partition
        # active in the Live session.  That open block device prevents the
        # kernel from accepting a replacement partition table.  Disable only
        # the selected disk's expected swap partition; never use swapoff -a.
        deactivate_target_swap(context, self.runner)
        self._settle_existing_partition_table(context, strict=False)

        for index, command in enumerate(commands.partition):
            if index == 0:
                result = self.runner.run(
                    command, check=False, timeout=60
                )
                if result.returncode != 0:
                    context.log(
                        "Partition table update was not accepted; "
                        "settling the selected disk and retrying once"
                    )
                    deactivate_target_swap(context, self.runner)
                    self._settle_existing_partition_table(
                        context, strict=False
                    )
                    self.runner.run(command, timeout=60)
            else:
                self.runner.run(command, timeout=60)
        self.runner.run(
            ("partprobe", context.plan.storage.disk.path), timeout=30
        )
        self.runner.run(("udevadm", "settle", "--timeout=30"), timeout=35)
        for device in commands.devices.values():
            if not Path(device).exists():
                raise RuntimeError(f"Partition device did not appear: {device}")
        for command in commands.format:
            self.runner.run(command, timeout=300)

    def verify(self, context: InstallContext) -> None:
        devices = context.values["partition_devices"]
        expected = {
            "efi-system": "vfat",
            "swap": "swap",
            "root": context.plan.storage.filesystem.value,
        }
        for name, filesystem in expected.items():
            result = self.runner.run(
                ("blkid", "-s", "TYPE", "-o", "value", devices[name]),
                timeout=10,
            )
            actual = result.stdout.strip()
            if actual != filesystem:
                raise RuntimeError(
                    f"{name} has filesystem {actual!r}, expected {filesystem!r}"
                )

    def cleanup(self, context: InstallContext) -> None:
        # Partitioning cannot be rolled back. Later mount steps own unmounting,
        # while this step owns any swap area created on the selected disk.
        deactivate_target_swap(context, self.runner, strict=False)
        self._settle_existing_partition_table(context, strict=False)

    def _settle_existing_partition_table(
        self, context: InstallContext, *, strict: bool = True
    ) -> None:
        disk = context.plan.storage.disk.path
        result = self.runner.run(
            ("partprobe", disk),
            check=False,
            timeout=30,
        )
        if strict and result.returncode != 0:
            raise RuntimeError(
                f"Could not refresh the selected disk partition table: {disk}"
            )
        self.runner.run(
            ("udevadm", "settle", "--timeout=30"),
            check=strict,
            timeout=35,
        )


@dataclass
class MountTargetStep:
    runner: CommandRunner
    target: Path = Path("/target")
    id: str = "mount-target"
    title: str = "Mount target filesystems"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 3
    destructive: bool = False

    def preflight(self, context: InstallContext) -> None:
        commands = ["mount", "umount", "findmnt"]
        if context.plan.storage.filesystem is Filesystem.BTRFS:
            commands.append("btrfs")
        self.runner.require_commands(commands)
        result = self.runner.run(
            ("findmnt", "--noheadings", "--mountpoint", str(self.target)),
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            raise RuntimeError(f"Target is already mounted: {self.target}")

    def execute(self, context: InstallContext) -> None:
        devices = context.values["partition_devices"]
        self.target.mkdir(parents=True, exist_ok=True)
        root = devices["root"]
        if context.plan.storage.filesystem is Filesystem.BTRFS:
            self.runner.run(("mount", root, str(self.target)), timeout=30)
            context.values["target_top_level_mounted"] = True
            for subvolume in BTRFS_SUBVOLUMES:
                self.runner.run(
                    (
                        "btrfs",
                        "subvolume",
                        "create",
                        str(self.target / subvolume.name),
                    ),
                    timeout=30,
                )
            self.runner.run(("umount", str(self.target)), timeout=30)
            context.values["target_top_level_mounted"] = False

            mounted: list[Path] = []
            context.values["target_btrfs_mounts"] = mounted
            for subvolume in BTRFS_SUBVOLUMES:
                mount_path = (
                    self.target
                    if subvolume.mount_point == "/"
                    else self.target / subvolume.mount_point.lstrip("/")
                )
                mount_path.mkdir(parents=True, exist_ok=True)
                self.runner.run(
                    (
                        "mount",
                        "-o",
                        subvolume.mount_options.removeprefix("defaults,"),
                        root,
                        str(mount_path),
                    ),
                    timeout=30,
                )
                mounted.append(mount_path)
        else:
            self.runner.run(
                ("mount", "-o", "noatime", root, str(self.target)), timeout=30
            )
            context.values["target_root_mounted"] = True

        efi_path = self.target / "boot/efi"
        efi_path.mkdir(parents=True, exist_ok=True)
        self.runner.run(
            ("mount", devices["efi-system"], str(efi_path)), timeout=30
        )
        context.values["target_efi_mounted"] = True
        context.values["target"] = self.target

    def verify(self, context: InstallContext) -> None:
        devices = context.values["partition_devices"]
        expected_sources = {
            self.target: devices["root"],
            self.target / "boot/efi": devices["efi-system"],
        }
        for path in context.values.get("target_btrfs_mounts", [])[1:]:
            expected_sources[path] = devices["root"]
        for path, expected_source in expected_sources.items():
            result = self.runner.run(
                (
                    "findmnt",
                    "--noheadings",
                    "--output",
                    "SOURCE",
                    "--mountpoint",
                    str(path),
                ),
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Mount verification failed: {path}")
            actual_source = result.stdout.strip().split("[", 1)[0]
            if not actual_source or os.path.realpath(
                actual_source
            ) != os.path.realpath(expected_source):
                raise RuntimeError(
                    f"Mount source mismatch for {path}: expected "
                    f"{expected_source}, found {actual_source or 'nothing'}"
                )
            context.log(
                f"Verified mount source: {path} <- {expected_source}"
            )

    def cleanup(self, context: InstallContext) -> None:
        if context.values.get("target_efi_mounted"):
            self.runner.run(
                ("umount", str(self.target / "boot/efi")),
                check=False,
                timeout=30,
            )
            context.values["target_efi_mounted"] = False
        for path in reversed(context.values.get("target_btrfs_mounts", [])):
            self.runner.run(
                ("umount", str(path)), check=False, timeout=30
            )
        context.values["target_btrfs_mounts"] = []
        if context.values.get("target_root_mounted"):
            self.runner.run(
                ("umount", str(self.target)), check=False, timeout=30
            )
            context.values["target_root_mounted"] = False
        if context.values.get("target_top_level_mounted"):
            self.runner.run(
                ("umount", str(self.target)), check=False, timeout=30
            )
            context.values["target_top_level_mounted"] = False


def deactivate_target_swap(
    context: InstallContext,
    runner: CommandRunner,
    *,
    strict: bool = True,
) -> bool:
    """Disable only this plan's target swap partition when it is active."""
    devices = context.values.get("partition_devices")
    if isinstance(devices, dict) and devices.get("swap"):
        swap_device = str(devices["swap"])
    else:
        swap_device = partition_path(context.plan.storage.disk.path, 3)

    result = runner.run(
        ("swapon", "--show=NAME", "--noheadings", "--raw"),
        check=False,
        timeout=10,
        log_output=False,
    )
    if result.returncode != 0:
        if strict:
            raise RuntimeError("Could not inspect active swap devices")
        return False

    expected = os.path.realpath(swap_device)
    active = {
        os.path.realpath(line.strip())
        for line in result.stdout.splitlines()
        if line.strip()
    }
    if expected not in active:
        return False

    context.log(
        f"Deactivating target swap from an earlier attempt: {swap_device}"
    )
    disabled = runner.run(
        ("swapoff", swap_device),
        check=False,
        timeout=60,
    )
    if disabled.returncode != 0:
        if strict:
            raise RuntimeError(
                f"Could not deactivate target swap: {swap_device}"
            )
        return False
    return True
