import unittest

from helpers import valid_plan
from installer_core.boot_commands import build_boot_commands, guided_loader_path
from installer_core.model import Architecture, Firmware, SecureBoot


class BootCommandPlanTests(unittest.TestCase):
    def test_amd64_uefi_installs_bios_grub_and_vendor_loader(self):
        commands = build_boot_commands(valid_plan(), "/target")
        self.assertEqual(len(commands.installs), 2)
        self.assertIn("--target=i386-pc", commands.installs[0])
        self.assertEqual(commands.installs[0][-1], "/dev/nvme0n1")
        self.assertIn("--target=x86_64-efi", commands.installs[1])
        self.assertNotIn("--force-extra-removable", commands.installs[1])
        self.assertIn("--no-extra-removable", commands.installs[1])
        self.assertIn("--no-nvram", commands.installs[1])
        self.assertIn("--uefi-secure-boot", commands.installs[1])
        self.assertEqual(commands.efi_fallback, "")
        self.assertEqual(
            commands.loader_path, r"\EFI\AnduinOS\shimx64.efi"
        )
        self.assertEqual(commands.nvram_create[:2], ("efibootmgr", "--create"))
        self.assertIn("2", commands.nvram_create)
        self.assertTrue(commands.bios_required)

    def test_arm64_installs_only_arm64_uefi(self):
        commands = build_boot_commands(
            valid_plan(architecture=Architecture.ARM64), "/target"
        )
        self.assertEqual(len(commands.installs), 1)
        self.assertIn("--target=arm64-efi", commands.installs[0])
        self.assertNotIn("--target=i386-pc", commands.installs[0])
        self.assertEqual(commands.efi_fallback, "")
        self.assertIn("--no-extra-removable", commands.installs[0])
        self.assertEqual(
            commands.loader_path, r"\EFI\AnduinOS\shimaa64.efi"
        )
        self.assertFalse(commands.bios_required)

    def test_uefi_secure_boot_flag_tracks_firmware_state(self):
        cases = (
            (
                valid_plan(),
                "--target=x86_64-efi",
                True,
            ),
            (
                valid_plan(secure_boot=SecureBoot.DISABLED),
                "--target=x86_64-efi",
                False,
            ),
            (
                valid_plan(
                    architecture=Architecture.ARM64,
                    secure_boot=SecureBoot.DISABLED,
                ),
                "--target=arm64-efi",
                False,
            ),
            (
                valid_plan(secure_boot=SecureBoot.UNSUPPORTED),
                "--target=x86_64-efi",
                False,
            ),
        )
        for plan, target_flag, secure_flag_expected in cases:
            with self.subTest(platform=plan.platform):
                commands = build_boot_commands(plan, "/target")
                efi = next(
                    command
                    for command in commands.installs
                    if target_flag in command
                )
                self.assertEqual(
                    "--uefi-secure-boot" in efi,
                    secure_flag_expected,
                )

    def test_amd64_bios_plan_keeps_disk_portable_to_uefi(self):
        plan = valid_plan(
            firmware=Firmware.BIOS,
            secure_boot=SecureBoot.NOT_APPLICABLE,
        )
        commands = build_boot_commands(plan, "/target")
        self.assertEqual(
            [command[3] for command in commands.installs],
            ["--target=i386-pc", "--target=x86_64-efi"],
        )
        self.assertFalse(
            any("--uefi-secure-boot" in command for command in commands.installs)
        )
        self.assertNotIn("--no-extra-removable", commands.installs[1])
        self.assertEqual(commands.efi_fallback, "EFI/BOOT/BOOTX64.EFI")
        self.assertEqual(commands.nvram_create, ())

    def test_guided_loader_path_tracks_architecture_and_secure_boot(self):
        self.assertEqual(
            guided_loader_path(valid_plan()),
            r"\EFI\AnduinOS\shimx64.efi",
        )
        self.assertEqual(
            guided_loader_path(valid_plan(architecture=Architecture.ARM64)),
            r"\EFI\AnduinOS\shimaa64.efi",
        )
        self.assertEqual(
            guided_loader_path(valid_plan(secure_boot=SecureBoot.DISABLED)),
            r"\EFI\AnduinOS\grubx64.efi",
        )
