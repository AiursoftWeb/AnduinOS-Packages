import unittest
from dataclasses import replace
from unittest.mock import patch

from test_manual_layout import selection
from test_manual_storage_graph import manual_plan
from installer_core.esp import (
    GUIDED_ESP_MINIMUM_FREE_BYTES,
    EspReuseInspection,
    NvramInspection,
)
from installer_core.manual_layout import (
    ManualPartitionRequest,
    ManualPartitionResizeRequest,
    ManualPartitionRole,
)
from installer_core.model import Filesystem
from installer_core.storage_commands import (
    StorageCommandPlan,
    build_manual_storage_commands,
)
from installer_core.storage_planning import (
    build_manual_storage_execution_plan,
)
from installer_core.storage_write_set import StorageAction
from installer_core.ntfs_resize import (
    MIB,
    NtfsResizeBlockReason,
    NtfsResizeInspection,
)


def healthy_esp(inventory):
    esp = inventory.disks[0].partitions[0]
    return EspReuseInspection(
        partuuid=esp.identity.partuuid,
        filesystem_uuid=esp.filesystem_uuid,
        healthy=True,
        free_bytes=GUIDED_ESP_MINIMUM_FREE_BYTES,
    )


def build_execution(*, chosen=None, filesystem=Filesystem.BTRFS):
    plan, inventory = manual_plan(
        chosen=chosen,
        filesystem=filesystem,
    )
    reuses_esp = bool(chosen.reused_esp_partuuid) if chosen else True
    execution = build_manual_storage_execution_plan(
        plan,
        inventory,
        esp_inspection=healthy_esp(inventory) if reuses_esp else None,
        nvram_inspection=NvramInspection(available=True),
    )
    return plan, inventory, execution


class ManualStorageExecutionPlanTests(unittest.TestCase):
    def test_classic_root_filesystems_use_exact_formatters_without_subvolumes(self):
        expected_commands = {
            Filesystem.EXT4: ("mkfs.ext4", "-F", "-L", "AnduinOS"),
            Filesystem.XFS: ("mkfs.xfs", "-f", "-L", "AnduinOS"),
            Filesystem.F2FS: ("mkfs.f2fs", "-f", "-l", "AnduinOS"),
        }
        for filesystem, prefix in expected_commands.items():
            with self.subTest(filesystem=filesystem.value):
                _plan, _inventory, execution = build_execution(
                    filesystem=filesystem
                )
                root = execution.commands.devices["root"]
                self.assertIn(
                    (*prefix, root),
                    execution.commands.format,
                )
                root_create = next(
                    command
                    for command in execution.commands.partition
                    if "mkpart" in command and "root" in command
                )
                if filesystem in {Filesystem.XFS, Filesystem.F2FS}:
                    self.assertNotIn(filesystem.value, root_create)
                self.assertFalse(
                    any(
                        item.action is StorageAction.CREATE_SUBVOLUME
                        for item in execution.write_set.operations
                    )
                )

    def test_ntfs_resize_compiles_only_to_typed_resize_data(self):
        from test_manual_layout import manual_disk

        disk = manual_disk()
        disk = replace(
            disk,
            partitions=(
                disk.partitions[0],
                replace(disk.partitions[1], filesystem_type="ntfs"),
            ),
        )
        request = ManualPartitionResizeRequest(
            "part-2",
            disk.partitions[1].identity.size_bytes,
            40 * 1024,
        )
        chosen = selection(
            resized=(request,),
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025 + 40 * 1024,
                    70 * 1024,
                ),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen, disk=disk)
        resize_inspection = NtfsResizeInspection(
            device="/dev/nvme0n1p2",
            filesystem="ntfs",
            current_size_bytes=request.original_size_bytes,
            minimum_size_bytes=4 * 1024 * MIB,
            maximum_size_bytes=request.original_size_bytes - MIB,
            block_reason=NtfsResizeBlockReason.NONE,
            message="safe",
            probe_exit_code=0,
            target_size_bytes=request.target_size_mib * MIB,
        )
        execution = build_manual_storage_execution_plan(
            plan,
            inventory,
            esp_inspection=healthy_esp(inventory),
            nvram_inspection=NvramInspection(available=True),
            ntfs_resize_inspections=(resize_inspection,),
        )
        self.assertEqual(len(execution.commands.ntfs_resizes), 1)
        resize = execution.commands.ntfs_resizes[0]
        self.assertEqual(resize.device, "/dev/nvme0n1p2")
        self.assertEqual(resize.target_size_bytes, 40 * 1024**3)
        self.assertEqual(
            resize.target_end_bytes,
            disk.partitions[1].identity.start_bytes + 40 * 1024**3 - 1,
        )
        self.assertFalse(
            any("resizepart" in item for item in execution.commands.partition)
        )
        operation = next(
            item
            for item in execution.write_set.operations
            if item.action is StorageAction.RESIZE_PARTITION
        )
        self.assertTrue(operation.destructive)
        self.assertEqual(operation.detail("target_size_bytes"), str(40 * 1024**3))

    def test_reused_esp_and_uninvolved_partition_are_preserved(self):
        _plan, _inventory, execution = build_execution()
        self.assertTrue(execution.reuses_esp)
        self.assertEqual(
            execution.commands.devices["efi-system"],
            "/dev/nvme0n1p1",
        )
        formatted = {item[-1] for item in execution.commands.format}
        self.assertNotIn("/dev/nvme0n1p1", formatted)
        self.assertEqual(
            len(
                tuple(
                    item
                    for item in execution.write_set.operations
                    if item.action is StorageAction.PRESERVE
                )
            ),
            2,
        )

    def test_declared_delete_uses_current_partition_number_only(self):
        chosen = selection(
            deleted=("part-2",),
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025,
                    70_000,
                ),
                ManualPartitionRequest(
                    ManualPartitionRole.SWAP,
                    70_000,
                    71_537,
                ),
            ),
        )
        plan, _inventory, execution = build_execution(chosen=chosen)
        self.assertEqual(
            execution.commands.partition[0],
            ("parted", "--script", "/dev/nvme0n1", "rm", "2"),
        )
        self.assertEqual(plan.storage.swap_size_mib, 1537)
        self.assertFalse(
            any("resizepart" in item or "move" in item
                for item in execution.commands.partition)
        )

    def test_reinitialize_gpt_creates_new_esp_without_individual_deletes(self):
        chosen = selection(
            reinitialize=True,
            reused_esp="",
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.EFI_SYSTEM,
                    1,
                    1025,
                ),
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025,
                    60_000,
                ),
            ),
        )
        _plan, _inventory, execution = build_execution(chosen=chosen)
        self.assertFalse(execution.reuses_esp)
        self.assertEqual(
            execution.commands.partition[0],
            ("parted", "--script", "/dev/nvme0n1", "mklabel", "gpt"),
        )
        self.assertIn(
            (
                "parted",
                "--script",
                "/dev/nvme0n1",
                "set",
                "1",
                "esp",
                "on",
            ),
            execution.commands.partition,
        )
        self.assertTrue(
            any(item[0] == "mkfs.vfat" for item in execution.commands.format)
        )
        self.assertFalse(
            any("rm" in item for item in execution.commands.partition)
        )

    def test_current_device_path_is_resolved_after_stable_identity(self):
        plan, inventory = manual_plan()
        disk = inventory.disks[0]
        current_partitions = tuple(
            replace(
                item,
                identity=replace(
                    item.identity,
                    path=item.identity.path.replace("nvme0n1", "nvme7n1"),
                ),
            )
            for item in disk.partitions
        )
        current_disk = replace(
            disk,
            identity=replace(disk.identity, path="/dev/nvme7n1"),
            partitions=current_partitions,
        )
        current_inventory = replace(inventory, disks=(current_disk,))
        execution = build_manual_storage_execution_plan(
            plan,
            current_inventory,
            esp_inspection=healthy_esp(current_inventory),
            nvram_inspection=NvramInspection(available=True),
        )
        self.assertEqual(
            execution.commands.devices["efi-system"],
            "/dev/nvme7n1p1",
        )
        self.assertTrue(
            all(
                command[2] == "/dev/nvme7n1"
                for command in execution.commands.partition
            )
        )

    def test_resize_or_move_command_injection_fails_closed(self):
        plan, inventory = manual_plan()
        commands = build_manual_storage_commands(plan, inventory)
        for injected in (
            ("parted", "--script", "/dev/nvme0n1", "resizepart", "2", "1MiB"),
            ("parted", "--script", "/dev/nvme0n1", "move", "2"),
        ):
            with self.subTest(command=injected):
                drifted = StorageCommandPlan(
                    partition=(injected, *commands.partition),
                    format=commands.format,
                    devices=commands.devices,
                )
                with patch(
                    "installer_core.storage_planning."
                    "build_manual_storage_commands",
                    return_value=drifted,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "forbidden geometry edit",
                    ):
                        build_manual_storage_execution_plan(
                            plan,
                            inventory,
                            esp_inspection=healthy_esp(inventory),
                            nvram_inspection=NvramInspection(True),
                        )

    def test_existing_esp_format_injection_fails_closed(self):
        plan, inventory = manual_plan()
        commands = build_manual_storage_commands(plan, inventory)
        drifted = replace(
            commands,
            format=(
                *commands.format,
                (
                    "mkfs.vfat",
                    "-F",
                    "32",
                    "-n",
                    "ATTACK",
                    commands.devices["efi-system"],
                ),
            ),
        )
        with patch(
            "installer_core.storage_planning.build_manual_storage_commands",
            return_value=drifted,
        ):
            with self.assertRaisesRegex(RuntimeError, "format commands drifted"):
                build_manual_storage_execution_plan(
                    plan,
                    inventory,
                    esp_inspection=healthy_esp(inventory),
                    nvram_inspection=NvramInspection(True),
                )

    def test_only_deleted_existing_swap_is_selected_for_deactivation(self):
        chosen = selection(
            deleted=("part-2",),
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025,
                    70_000,
                ),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen)
        disk = inventory.disks[0]
        current = replace(
            disk,
            partitions=(
                disk.partitions[0],
                replace(disk.partitions[1], filesystem_type="swap"),
            ),
        )
        commands = build_manual_storage_commands(
            plan,
            replace(inventory, disks=(current,)),
        )
        self.assertEqual(
            commands.deactivate_swap_devices,
            ("/dev/nvme0n1p2",),
        )


if __name__ == "__main__":
    unittest.main()
