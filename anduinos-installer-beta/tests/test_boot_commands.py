import unittest
from dataclasses import replace

from helpers import valid_plan
from installer_core.boot_commands import (
    build_boot_commands, guided_loader_path,
    build_guided_coexistence_boot_commands, build_manual_boot_commands,
)
from installer_core.model import Architecture, Firmware, InstallMode, SecureBoot


class BootCommandPlanTests(unittest.TestCase):
    def test_manual_and_coexistence_use_shim_before_secure_boot_is_enabled(self):
        for architecture, suffix in ((Architecture.AMD64, "x64"), (Architecture.ARM64, "aa64")):
            for mode, build in ((InstallMode.MANUAL, build_manual_boot_commands),
                                (InstallMode.GUIDED_COEXISTENCE, build_guided_coexistence_boot_commands)):
                with self.subTest(architecture=architecture, mode=mode):
                    base = valid_plan(architecture=architecture, secure_boot=SecureBoot.DISABLED)
                    plan = replace(base, storage=replace(base.storage, mode=mode))
                    commands = build(plan, "/target", disk_path="/dev/test", esp_partition_number=1)
                    self.assertIn("--uefi-secure-boot", commands.install)
                    self.assertIn("--no-extra-removable", commands.install)
                    self.assertEqual(commands.loader_path, rf"\EFI\AnduinOS\shim{suffix}.efi")

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

    def test_external_uefi_keeps_nvram_and_adds_direct_portable_fallback(self):
        commands = build_boot_commands(
            valid_plan(external_target=True), "/target"
        )
        efi = commands.installs[1]
        self.assertIn("--no-extra-removable", efi)
        self.assertIn("--uefi-secure-boot", efi)
        self.assertEqual(commands.efi_fallback, "EFI/BOOT/BOOTX64.EFI")
        self.assertEqual(
            commands.loader_path, r"\EFI\AnduinOS\shimx64.efi"
        )
        self.assertEqual(commands.nvram_create[:2], ("efibootmgr", "--create"))

    def test_external_arm64_uses_the_standard_portable_loader_name(self):
        commands = build_boot_commands(
            valid_plan(
                architecture=Architecture.ARM64,
                external_target=True,
            ),
            "/target",
        )
        self.assertEqual(commands.efi_fallback, "EFI/BOOT/BOOTAA64.EFI")
        self.assertIn("--no-extra-removable", commands.installs[0])
        self.assertEqual(
            commands.loader_path, r"\EFI\AnduinOS\shimaa64.efi"
        )

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

    def test_uefi_always_installs_signed_chain_even_before_enforcement(self):
        cases = (
            (
                valid_plan(),
                "--target=x86_64-efi",
                True,
            ),
            (
                valid_plan(secure_boot=SecureBoot.DISABLED),
                "--target=x86_64-efi",
                True,
            ),
            (
                valid_plan(
                    architecture=Architecture.ARM64,
                    secure_boot=SecureBoot.DISABLED,
                ),
                "--target=arm64-efi",
                True,
            ),
            (
                valid_plan(secure_boot=SecureBoot.UNSUPPORTED),
                "--target=x86_64-efi",
                True,
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
            r"\EFI\AnduinOS\shimx64.efi",
        )
