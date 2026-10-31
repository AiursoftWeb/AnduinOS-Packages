import unittest
from dataclasses import replace
from unittest.mock import patch

from helpers import valid_plan
from installer_core.layout import build_erase_disk_layout
from installer_core.model import Architecture, Filesystem, Firmware, SecureBoot
from installer_core.storage_commands import (
    StorageCommandPlan,
    build_storage_commands,
)
from installer_core.storage_planning import build_erase_disk_execution_plan
from installer_core.storage_write_set import (
    StorageAction,
    build_erase_disk_write_set,
)


class EraseDiskExecutionPlanTests(unittest.TestCase):
    def test_boot_writes_match_firmware_and_portability_in_complete_plan(self):
        platforms = (
            (Architecture.AMD64, Firmware.UEFI, SecureBoot.ENABLED),
            (Architecture.AMD64, Firmware.UEFI, SecureBoot.DISABLED),
            (Architecture.ARM64, Firmware.UEFI, SecureBoot.ENABLED),
            (Architecture.ARM64, Firmware.UEFI, SecureBoot.DISABLED),
            (Architecture.AMD64, Firmware.BIOS, SecureBoot.NOT_APPLICABLE),
        )
        for architecture, firmware, secure_boot in platforms:
            for filesystem in (Filesystem.BTRFS, Filesystem.EXT4):
                for external in (False, True):
                    with self.subTest(
                        architecture=architecture,
                        firmware=firmware,
                        secure_boot=secure_boot,
                        filesystem=filesystem,
                        external=external,
                    ):
                        plan = valid_plan(
                            architecture=architecture,
                            firmware=firmware,
                            secure_boot=secure_boot,
                            filesystem=filesystem,
                            external_target=external,
                        )
                        execution = build_erase_disk_execution_plan(plan)
                        expected_actions = [
                            StorageAction.WRITE_BOOT_FILES,
                            StorageAction.WRITE_FALLBACK_BOOT_FILES,
                        ]
                        if firmware is Firmware.UEFI:
                            expected_actions.append(StorageAction.UPDATE_NVRAM)
                        boot_writes = execution.write_set.operations[
                            -len(expected_actions):
                        ]
                        self.assertEqual(
                            [item.action for item in boot_writes],
                            expected_actions,
                        )
                        self.assertEqual(
                            {item.target_id for item in boot_writes},
                            {plan.storage.graph.boot_targets[0].efi_filesystem_id},
                        )

    def test_composes_commands_and_write_set_from_one_layout(self):
        execution = build_erase_disk_execution_plan(valid_plan())
        self.assertEqual(
            set(execution.commands.devices),
            {item.name for item in execution.layout.partitions},
        )
        declared = {
            item.detail("name"): item.display_path
            for item in execution.write_set.operations
            if item.action is StorageAction.CREATE_PARTITION
        }
        self.assertEqual(declared, execution.commands.devices)

    def test_ext4_parity_has_one_root_format_and_no_subvolumes(self):
        execution = build_erase_disk_execution_plan(
            valid_plan(filesystem=Filesystem.EXT4)
        )
        self.assertTrue(
            any(command[0] == "mkfs.ext4" for command in execution.commands.format)
        )
        self.assertFalse(
            any(
                item.action is StorageAction.CREATE_SUBVOLUME
                for item in execution.write_set.operations
            )
        )

    def test_fails_closed_when_declared_formats_drift(self):
        write_set = build_erase_disk_write_set(valid_plan())
        operations = tuple(
            replace(
                item,
                details=(("filesystem", "ext4"),),
            )
            if item.action is StorageAction.FORMAT
            and item.detail("filesystem") == "btrfs"
            else item
            for item in write_set.operations
        )
        drifted = replace(write_set, operations=operations)
        with patch(
            "installer_core.storage_planning.build_erase_disk_write_set",
            return_value=drifted,
        ):
            with self.assertRaisesRegex(RuntimeError, "formats drifted"):
                build_erase_disk_execution_plan(valid_plan())

    def test_fails_closed_when_executable_format_commands_drift(self):
        plan = valid_plan()
        commands = build_storage_commands(plan, build_erase_disk_layout(plan))
        drifted_formats = tuple(
            ("mkfs.ext4", "-F", "-L", "AnduinOS", command[-1])
            if command[0] == "mkfs.btrfs"
            else command
            for command in commands.format
        )
        drifted = StorageCommandPlan(
            partition=commands.partition,
            format=drifted_formats,
            devices=commands.devices,
        )
        with patch(
            "installer_core.storage_planning.build_storage_commands",
            return_value=drifted,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "format commands do not match"
            ):
                build_erase_disk_execution_plan(plan)


if __name__ == "__main__":
    unittest.main()
