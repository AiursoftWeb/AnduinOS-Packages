import unittest

from helpers import valid_plan
from installer_core.layout import build_erase_disk_layout
from installer_core.boot_commands import build_boot_commands
from installer_core.model import Architecture, DiskIdentity, Filesystem
from installer_core.storage_commands import (
    build_storage_commands,
    partition_path,
)


class StorageCommandTests(unittest.TestCase):
    def test_partition_device_names(self):
        self.assertEqual(partition_path("/dev/sda", 2), "/dev/sda2")
        self.assertEqual(partition_path("/dev/nvme0n1", 2), "/dev/nvme0n1p2")
        self.assertEqual(partition_path("/dev/mmcblk0", 2), "/dev/mmcblk0p2")

    def test_amd64_commands_include_bios_esp_swap_and_btrfs(self):
        plan = valid_plan()
        commands = build_storage_commands(
            plan, build_erase_disk_layout(plan)
        )
        self.assertEqual(commands.devices["bios-boot"], "/dev/nvme0n1p1")
        self.assertEqual(commands.devices["efi-system"], "/dev/nvme0n1p2")
        self.assertEqual(commands.devices["swap"], "/dev/nvme0n1p3")
        self.assertEqual(commands.devices["root"], "/dev/nvme0n1p4")
        self.assertIn(
            (
                "parted",
                "--script",
                "/dev/nvme0n1",
                "set",
                "1",
                "bios_grub",
                "on",
            ),
            commands.partition,
        )
        self.assertIn(
            ("mkswap", "-L", "AnduinOS-swap", "/dev/nvme0n1p3"),
            commands.format,
        )
        self.assertIn(
            (
                "mkfs.btrfs",
                "--force",
                "--label",
                "AnduinOS",
                "/dev/nvme0n1p4",
            ),
            commands.format,
        )

    def test_arm64_ext4_has_no_bios_partition(self):
        plan = valid_plan(
            architecture=Architecture.ARM64,
            filesystem=Filesystem.EXT4,
        )
        commands = build_storage_commands(
            plan, build_erase_disk_layout(plan)
        )
        self.assertNotIn("bios-boot", commands.devices)
        self.assertEqual(commands.devices["efi-system"], "/dev/nvme0n1p1")
        self.assertEqual(commands.devices["swap"], "/dev/nvme0n1p2")
        self.assertEqual(commands.devices["root"], "/dev/nvme0n1p3")
        self.assertIn(
            (
                "mkfs.ext4",
                "-F",
                "-L",
                "AnduinOS",
                "/dev/nvme0n1p3",
            ),
            commands.format,
        )

    def test_windows_disk_is_absent_from_selected_disk_write_plan(self):
        plan = valid_plan(
            disk=DiskIdentity(
                path="/dev/sdb",
                stable_id="serial:anduinos-target",
                expected_size_bytes=128 * 1024**3,
            ),
        )
        storage = build_storage_commands(
            plan, build_erase_disk_layout(plan)
        )
        boot = build_boot_commands(plan, "/target")
        commands = (
            *storage.partition,
            *storage.format,
            *boot.installs,
        )
        flattened = "\n".join(" ".join(command) for command in commands)

        self.assertNotIn("/dev/sda", flattened)
        self.assertIn("/dev/sdb", flattened)
        self.assertIn("/dev/sdb2", flattened)
        self.assertIn(
            ("chroot", "/target", "grub-install", "--target=i386-pc",
             "--recheck", "/dev/sdb"),
            boot.installs,
        )

    def test_zero_disk_swap_has_no_device_partition_or_formatter(self):
        plan = valid_plan(swap_size_mib=0)
        commands = build_storage_commands(
            plan, build_erase_disk_layout(plan)
        )
        self.assertEqual(
            set(commands.devices),
            {"bios-boot", "efi-system", "root"},
        )
        self.assertFalse(
            any(command[0] == "mkswap" for command in commands.format)
        )
        self.assertEqual(commands.devices["root"], "/dev/nvme0n1p3")
