"""Fatal storage steps owned by the trusted executor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .btrfs import BTRFS_SUBVOLUMES
from .command import CommandRunner
from .esp import inspect_esp_for_reuse, inspect_nvram
from .execution_boundaries import emit_boundary
from .model import Architecture, Filesystem, Firmware, InstallMode
from .ntfs_resize import (
    NTFS_RESIZE,
    inspect_ntfs_resize_with_runner,
)
from .steps import FailurePolicy, InstallContext
from .storage_commands import build_manual_storage_commands, partition_path
from .storage_inventory import probe_storage_inventory
from .storage_planning import (
    EraseDiskExecutionPlan,
    GuidedCoexistenceExecutionPlan,
    ManualStorageExecutionPlan,
    build_erase_disk_execution_plan,
    build_guided_coexistence_execution_plan,
    build_manual_storage_execution_plan,
    resolve_guided_esp_partition,
    resolve_manual_esp_partition,
)
from .storage_preservation import (
    GuidedPreservationSnapshot,
    ManualPreservationSnapshot,
    capture_guided_preservation_snapshot,
    capture_manual_preservation_snapshot,
    verify_guided_storage_result,
    verify_manual_storage_result,
)


@dataclass
class PrepareStorageStep:
    runner: CommandRunner
    target: Path = Path("/target")
    inventory_probe: object = probe_storage_inventory
    esp_inspector: object = inspect_esp_for_reuse
    nvram_inspector: object = inspect_nvram
    id: str = "prepare-storage"
    title: str = "Partition and format target disk"
    failure_policy: FailurePolicy = FailurePolicy.FATAL
    progress_weight: int = 10
    destructive: bool = True

    def preflight(self, context: InstallContext) -> None:
        context.validate_plan()
        storage_disk_path = context.plan.storage.disk.path
        if context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE:
            inventory = self.inventory_probe()
            storage_disk_path = inventory.disk(
                context.plan.storage.disk.stable_id
            ).identity.path
            esp, reuses_esp = resolve_guided_esp_partition(
                context.plan, inventory
            )
            esp_inspection = (
                self.esp_inspector(esp, self.runner)
                if reuses_esp
                else None
            )
            execution_plan = build_guided_coexistence_execution_plan(
                context.plan,
                inventory,
                esp_inspection=esp_inspection,
                nvram_inspection=self.nvram_inspector(self.runner),
                target=str(self.target),
            )
            preservation = capture_guided_preservation_snapshot(
                context.plan,
                inventory,
                execution_plan.write_set,
            )
            context.values["guided_storage_execution_plan"] = execution_plan
            context.values["guided_preservation_snapshot"] = preservation
            context.values["guided_esp_inspection"] = esp_inspection
        elif context.plan.storage.mode is InstallMode.MANUAL:
            inventory = self.inventory_probe()
            storage_disk_path = inventory.disk(
                context.plan.storage.disk.stable_id
            ).identity.path
            esp, reuses_esp = resolve_manual_esp_partition(
                context.plan, inventory
            )
            esp_inspection = (
                self.esp_inspector(esp, self.runner)
                if reuses_esp
                else None
            )
            preliminary_commands = build_manual_storage_commands(
                context.plan,
                inventory,
            )
            ntfs_resize_inspections = ()
            if preliminary_commands.ntfs_resizes:
                self.runner.require_commands(("ntfsresize", "ntfs-3g.probe"))
                ntfs_resize_inspections = tuple(
                    inspect_ntfs_resize_with_runner(
                        resize.device,
                        resize.original_size_bytes,
                        self.runner,
                        target_size_bytes=resize.target_size_bytes,
                    )
                    for resize in preliminary_commands.ntfs_resizes
                )
            execution_plan = build_manual_storage_execution_plan(
                context.plan,
                inventory,
                esp_inspection=esp_inspection,
                nvram_inspection=self.nvram_inspector(self.runner),
                ntfs_resize_inspections=ntfs_resize_inspections,
                target=str(self.target),
            )
            preservation = capture_manual_preservation_snapshot(
                context.plan,
                inventory,
                execution_plan.write_set,
            )
            context.values["manual_storage_execution_plan"] = execution_plan
            context.values["manual_preservation_snapshot"] = preservation
            context.values["manual_esp_inspection"] = esp_inspection
        else:
            if context.plan.platform.firmware is Firmware.UEFI:
                nvram = self.nvram_inspector(self.runner)
                if not nvram.available:
                    reason = nvram.reason or "UEFI variables are unavailable"
                    raise RuntimeError(
                        "Cannot safely create the AnduinOS firmware boot "
                        "entry: " + reason
                    )
                context.values["erase_disk_nvram_inspection"] = nvram
            execution_plan = build_erase_disk_execution_plan(context.plan)
            context.values["erase_disk_execution_plan"] = execution_plan
        context.values["storage_disk_path"] = storage_disk_path
        context.values["storage_execution_plan"] = execution_plan
        context.values["storage_write_set"] = execution_plan.write_set
        commands = [
            "parted",
            "partprobe",
            "udevadm",
            "mkfs.vfat",
            "swapon",
            "swapoff",
            "sync",
        ]
        if context.plan.storage.swap_size_mib:
            commands.append("mkswap")
        commands.append(
            {
                Filesystem.BTRFS: "mkfs.btrfs",
                Filesystem.EXT4: "mkfs.ext4",
                Filesystem.XFS: "mkfs.xfs",
                Filesystem.F2FS: "mkfs.f2fs",
            }[context.plan.storage.filesystem]
        )
        self.runner.require_commands(commands)

    def execute(self, context: InstallContext) -> None:
        execution_plan = context.values.get("storage_execution_plan")
        if (
            context.plan.storage.mode is InstallMode.GUIDED_COEXISTENCE
            and not isinstance(
                execution_plan, GuidedCoexistenceExecutionPlan
            )
        ):
            raise RuntimeError(
                "Guided storage was not frozen during all-step preflight"
            )
        if (
            context.plan.storage.mode is InstallMode.MANUAL
            and not isinstance(execution_plan, ManualStorageExecutionPlan)
        ):
            raise RuntimeError(
                "Manual storage was not frozen during all-step preflight"
            )
        if not isinstance(
            execution_plan,
            (
                EraseDiskExecutionPlan,
                GuidedCoexistenceExecutionPlan,
                ManualStorageExecutionPlan,
            ),
        ):
            # Unit-level callers may execute this step directly. The real
            # StepRunner always freezes the plan during the all-step preflight.
            execution_plan = build_erase_disk_execution_plan(context.plan)
            context.values["erase_disk_execution_plan"] = execution_plan
            context.values["storage_execution_plan"] = execution_plan
            context.values["storage_write_set"] = execution_plan.write_set
        commands = execution_plan.commands
        if isinstance(execution_plan, EraseDiskExecutionPlan):
            context.values["layout"] = execution_plan.layout
        context.values["partition_devices"] = commands.devices
        # A previous failed attempt can leave the newly-created swap partition
        # active in the Live session.  That open block device prevents the
        # kernel from accepting a replacement partition table.  Disable only
        # the selected disk's expected swap partition; never use swapoff -a.
        deactivate_target_swap(context, self.runner)
        self._settle_existing_partition_table(context, strict=False)

        guided = isinstance(execution_plan, GuidedCoexistenceExecutionPlan)
        manual = isinstance(execution_plan, ManualStorageExecutionPlan)
        boundary_prefix = "guided" if guided else "manual"
        if manual and execution_plan.commands.ntfs_resizes:
            self._execute_ntfs_resizes(context, execution_plan)
        for index, command in enumerate(commands.partition):
            boundary = f"{boundary_prefix}-partition-command-{index + 1}"
            if guided or manual:
                emit_boundary(context, boundary, "before")
            if index == 0 and not manual:
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
            if guided or manual:
                emit_boundary(context, boundary, "after")
        self.runner.run(
            ("partprobe", self._current_disk_path(context)), timeout=30
        )
        self.runner.run(("udevadm", "settle", "--timeout=30"), timeout=35)
        for device in commands.devices.values():
            if not Path(device).exists():
                raise RuntimeError(f"Partition device did not appear: {device}")
        device_names = {
            device: name for name, device in commands.devices.items()
        }
        for index, command in enumerate(commands.format):
            name = device_names.get(command[-1], str(index + 1))
            boundary = f"{boundary_prefix}-format-{name}"
            if guided or manual:
                emit_boundary(context, boundary, "before")
            self.runner.run(command, timeout=300)
            if guided or manual:
                emit_boundary(context, boundary, "after")
        self.runner.run(("udevadm", "settle", "--timeout=30"), timeout=35)

    def _execute_ntfs_resizes(
        self,
        context: InstallContext,
        execution_plan: ManualStorageExecutionPlan,
    ) -> None:
        for index, resize in enumerate(
            execution_plan.commands.ntfs_resizes,
            start=1,
        ):
            inspection = inspect_ntfs_resize_with_runner(
                resize.device,
                resize.original_size_bytes,
                self.runner,
                target_size_bytes=resize.target_size_bytes,
            )
            if not inspection.safe:
                raise RuntimeError(
                    "NTFS changed after preflight; shrinking is refused: "
                    + inspection.message
                )

            filesystem_boundary = f"manual-ntfs-filesystem-resize-{index}"
            emit_boundary(context, filesystem_boundary, "before")
            self.runner.run(
                (
                    NTFS_RESIZE,
                    "--size",
                    str(resize.target_size_bytes),
                    resize.device,
                ),
                input_text="y\n",
                timeout=7200,
            )
            self.runner.run(("sync",), timeout=300)
            emit_boundary(context, filesystem_boundary, "after")

            partition_boundary = f"manual-ntfs-partition-resize-{index}"
            emit_boundary(context, partition_boundary, "before")
            self.runner.run(
                (
                    "parted",
                    "--script",
                    resize.disk,
                    "unit",
                    "B",
                    "resizepart",
                    str(resize.partition_number),
                    f"{resize.target_end_bytes}B",
                ),
                timeout=300,
            )
            self.runner.run(("partprobe", resize.disk), timeout=30)
            self.runner.run(
                ("udevadm", "settle", "--timeout=30"), timeout=35
            )
            self._verify_resized_partition_geometry(
                context,
                resize,
            )
            emit_boundary(context, partition_boundary, "after")

    def _verify_resized_partition_geometry(
        self,
        context: InstallContext,
        resize,
    ) -> None:
        inventory = self.inventory_probe()
        try:
            disk = inventory.disk(context.plan.storage.disk.stable_id)
        except KeyError as error:
            raise RuntimeError(
                "The selected disk disappeared after shrinking NTFS"
            ) from error
        graph = context.plan.storage.graph
        assert graph is not None
        reference = next(
            item
            for item in graph.block_references
            if item.reference_id == resize.target_reference_id
        )
        partition = next(
            (
                item
                for item in disk.partitions
                if item.identity.partuuid == reference.stable_id
            ),
            None,
        )
        expected_start = resize.target_end_bytes - resize.target_size_bytes + 1
        if (
            partition is None
            or partition.identity.number != resize.partition_number
            or partition.identity.start_bytes != expected_start
            or partition.identity.size_bytes != resize.target_size_bytes
            or partition.filesystem_type.casefold() != "ntfs"
        ):
            raise RuntimeError(
                "The NTFS partition boundary did not match the declared "
                "resize; no new partition will be created"
            )
    def verify(self, context: InstallContext) -> None:
        devices = context.values["partition_devices"]
        expected = {
            "efi-system": "vfat",
            "root": context.plan.storage.filesystem.value,
        }
        if context.plan.storage.swap_size_mib:
            expected["swap"] = "swap"
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
        preservation = context.values.get("guided_preservation_snapshot")
        if isinstance(preservation, GuidedPreservationSnapshot):
            verify_guided_storage_result(
                context.plan,
                preservation,
                self.inventory_probe(),
            )
        manual_preservation = context.values.get(
            "manual_preservation_snapshot"
        )
        if isinstance(manual_preservation, ManualPreservationSnapshot):
            verify_manual_storage_result(
                context.plan,
                manual_preservation,
                self.inventory_probe(),
            )

    def cleanup(self, context: InstallContext) -> None:
        # Partitioning cannot be rolled back. Later mount steps own unmounting,
        # while this step owns any swap area created on the selected disk.
        deactivate_target_swap(context, self.runner, strict=False)
        self._settle_existing_partition_table(context, strict=False)

    def _settle_existing_partition_table(
        self, context: InstallContext, *, strict: bool = True
    ) -> None:
        disk = self._current_disk_path(context)
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

    @staticmethod
    def _current_disk_path(context: InstallContext) -> str:
        current = context.values.get("storage_disk_path")
        if isinstance(current, str) and current:
            return current
        return context.plan.storage.disk.path


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
    candidates: set[str] = set()
    if isinstance(devices, dict) and devices.get("swap"):
        candidates.add(str(devices["swap"]))
    execution_plan = context.values.get("storage_execution_plan")
    if isinstance(execution_plan, ManualStorageExecutionPlan):
        candidates.update(execution_plan.commands.deactivate_swap_devices)
    if context.plan.storage.mode is InstallMode.ERASE_DISK:
        legacy_number = (
            3
            if context.plan.platform.architecture is Architecture.AMD64
            else 2
        )
        candidates.add(
            partition_path(context.plan.storage.disk.path, legacy_number)
        )

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

    active = {
        os.path.realpath(line.strip())
        for line in result.stdout.splitlines()
        if line.strip()
    }
    swap_device = next(
        (
            candidate
            for candidate in candidates
            if os.path.realpath(candidate) in active
        ),
        "",
    )
    if not swap_device:
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
