import unittest

from helpers import valid_plan
from installer_core.boot_commands import build_boot_commands
from installer_core.model import Architecture


class BootCommandPlanTests(unittest.TestCase):
    def test_amd64_installs_bios_and_uefi_with_fallback(self):
        commands = build_boot_commands(valid_plan(), "/target")
        self.assertEqual(len(commands.installs), 2)
        self.assertIn("--target=i386-pc", commands.installs[0])
        self.assertEqual(commands.installs[0][-1], "/dev/nvme0n1")
        self.assertIn("--target=x86_64-efi", commands.installs[1])
        self.assertIn("--force-extra-removable", commands.installs[1])
        self.assertIn("--uefi-secure-boot", commands.installs[1])
        self.assertEqual(commands.efi_fallback, "EFI/BOOT/BOOTX64.EFI")
        self.assertTrue(commands.bios_required)

    def test_arm64_installs_only_arm64_uefi(self):
        commands = build_boot_commands(
            valid_plan(architecture=Architecture.ARM64), "/target"
        )
        self.assertEqual(len(commands.installs), 1)
        self.assertIn("--target=arm64-efi", commands.installs[0])
        self.assertNotIn("--target=i386-pc", commands.installs[0])
        self.assertEqual(commands.efi_fallback, "EFI/BOOT/BOOTAA64.EFI")
        self.assertFalse(commands.bios_required)
