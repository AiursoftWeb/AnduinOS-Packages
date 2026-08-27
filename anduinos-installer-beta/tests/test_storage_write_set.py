import unittest
from dataclasses import replace

from helpers import valid_plan
from installer_core.btrfs import BTRFS_SUBVOLUMES
from installer_core.boot_commands import build_boot_commands
from installer_core.layout import build_erase_disk_layout
from installer_core.model import Architecture, Filesystem
from installer_core.storage_commands import build_storage_commands
from installer_core.storage_write_set import (
    StorageAction,
    StorageObjectKind,
    build_erase_disk_write_set,
)


class StorageWriteSetTests(unittest.TestCase):
    def test_btrfs_write_set_covers_current_amd64_layout(self):
        plan = valid_plan()
        layout = build_erase_disk_layout(plan)
        commands = build_storage_commands(plan, layout)
        boot = build_boot_commands(plan, "/target")
        write_set = build_erase_disk_write_set(plan)

        self.assertEqual(write_set.disk_stable_id, plan.storage.disk.stable_id)
        self.assertEqual(
            [item.action for item in write_set.operations].count(
                StorageAction.REPLACE_PARTITION_TABLE
            ),
            1,
        )
        creates = [
            item
            for item in write_set.operations
            if item.action is StorageAction.CREATE_PARTITION
        ]
        self.assertEqual(len(creates), len(layout.partitions))
        self.assertEqual(
            {item.display_path for item in creates},
            set(commands.devices.values()),
        )

        formats = [
            item
            for item in write_set.operations
            if item.action is StorageAction.FORMAT
        ]
        self.assertEqual(
            {item.detail("filesystem") for item in formats},
            {"vfat", "swap", "btrfs"},
        )
        subvolumes = [
            item
            for item in write_set.operations
            if item.action is StorageAction.CREATE_SUBVOLUME
        ]
        self.assertEqual(
            [item.detail("name") for item in subvolumes],
            [item.name for item in BTRFS_SUBVOLUMES],
        )
        self.assertTrue(
            all(
                item.target_kind is StorageObjectKind.SUBVOLUME
                for item in subvolumes
            )
        )
        self.assertEqual(len(write_set.destructive_operations), 4)
        self.assertTrue(boot.bios_required)
        self.assertTrue(
            any(
                item.action is StorageAction.WRITE_BIOS_BOOTLOADER
                for item in write_set.operations
            )
        )
        fallback = next(
            item
            for item in write_set.operations
            if item.action is StorageAction.WRITE_FALLBACK_BOOT_FILES
        )
        self.assertEqual(fallback.detail("path"), boot.efi_fallback)
        self.assertTrue(
            any(
                item.action is StorageAction.CONFIGURE_MOUNTS
                for item in write_set.operations
            )
        )

    def test_ext4_write_set_has_no_subvolumes(self):
        base = valid_plan(filesystem=Filesystem.EXT4)
        write_set = build_erase_disk_write_set(base)
        self.assertFalse(
            any(
                item.action is StorageAction.CREATE_SUBVOLUME
                for item in write_set.operations
            )
        )
        root_format = next(
            item
            for item in write_set.operations
            if item.action is StorageAction.FORMAT
            and item.detail("filesystem") == "ext4"
        )
        self.assertTrue(root_format.display_path.endswith("p4"))

    def test_arm64_write_set_uses_arm_fallback_and_no_bios_partition(self):
        plan = valid_plan(architecture=Architecture.ARM64)
        write_set = build_erase_disk_write_set(plan)
        creates = [
            item
            for item in write_set.operations
            if item.action is StorageAction.CREATE_PARTITION
        ]
        self.assertEqual(len(creates), 3)
        self.assertNotIn(
            "bios-boot", {item.detail("name") for item in creates}
        )
        fallback = next(
            item
            for item in write_set.operations
            if item.action is StorageAction.WRITE_FALLBACK_BOOT_FILES
        )
        self.assertEqual(fallback.detail("path"), "EFI/BOOT/BOOTAA64.EFI")
        self.assertFalse(
            any(
                item.action is StorageAction.WRITE_BIOS_BOOTLOADER
                for item in write_set.operations
            )
        )

    def test_target_ids_never_depend_on_device_path(self):
        plan = valid_plan()
        moved = replace(
            plan,
            storage=replace(
                plan.storage,
                disk=replace(plan.storage.disk, path="/dev/nvme9n9"),
            ),
        )
        original = build_erase_disk_write_set(plan)
        changed = build_erase_disk_write_set(moved)
        self.assertEqual(
            [item.target_id for item in original.operations],
            [item.target_id for item in changed.operations],
        )
        self.assertNotEqual(
            [item.display_path for item in original.operations],
            [item.display_path for item in changed.operations],
        )

    def test_zero_disk_swap_declares_no_swap_write(self):
        write_set = build_erase_disk_write_set(
            valid_plan(swap_size_mib=0)
        )
        self.assertFalse(
            any(
                item.action is StorageAction.FORMAT
                and item.detail("filesystem") == "swap"
                for item in write_set.operations
            )
        )
        self.assertFalse(
            any(
                item.action is StorageAction.CREATE_PARTITION
                and item.detail("name") == "swap"
                for item in write_set.operations
            )
        )


if __name__ == "__main__":
    unittest.main()
