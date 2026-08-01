import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.bootloader import InstallBootloaderStep
from installer_core.esp import (
    NvramInspection,
    capture_preserved_esp_tree,
)
from installer_core.storage_planning import (
    build_guided_coexistence_execution_plan,
)
from installer_core.steps import InstallContext
from installer_core.validation import ExecutionPolicy
from test_guided_storage_graph import guided_plan
from test_guided_storage_planning import healthy_esp


def prepare_target(target: Path) -> None:
    for executable in (
        "usr/sbin/grub-install",
        "usr/sbin/update-grub",
        "usr/sbin/update-initramfs",
    ):
        path = target / executable
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (target / "boot/efi").mkdir(parents=True)


def write_pe(path: Path, machine: int) -> None:
    data = bytearray(70)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (64).to_bytes(4, "little")
    data[64:68] = b"PE\0\0"
    data[68:70] = machine.to_bytes(2, "little")
    path.write_bytes(data)


GRUB_INSTALL_HELP = """
--target=TARGET
--recheck
--efi-directory=DIR
--bootloader-id=ID
--no-nvram
--no-extra-removable
--uefi-secure-boot
"""


class InstallBootloaderTests(unittest.TestCase):
    def compatible_runner(self, target: Path) -> FakeRunner:
        runner = FakeRunner()
        runner.outputs[
            ("chroot", str(target), "grub-install", "--help")
        ] = (GRUB_INSTALL_HELP, "", 0)
        return runner

    def test_runs_initramfs_before_grub_install_and_update_grub_last(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                {"target": target, "target_efi_mounted": True},
            )
            step = InstallBootloaderStep(runner)
            step.preflight(context)
            step.execute(context)

        commands = [item[0] for item in runner.commands]
        self.assertEqual(commands[0][2:], ("grub-install", "--help"))
        self.assertFalse(runner.commands[0][1]["log_output"])
        self.assertEqual(commands[1][2], "update-initramfs")
        self.assertEqual(commands[-1][2], "update-grub")
        self.assertEqual(
            [
                command[3]
                for command in commands
                if command[2] == "grub-install"
                and command[3].startswith("--target=")
            ],
            ["--target=i386-pc", "--target=x86_64-efi"],
        )

    def test_verifies_matching_kernel_grub_bios_and_efi_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                {"target": target, "target_efi_mounted": True},
            )
            step = InstallBootloaderStep(runner)
            step.execute(context)

            (target / "boot/vmlinuz-6.14-test").touch()
            (target / "boot/initrd.img-6.14-test").touch()
            (target / "boot/grub").mkdir(exist_ok=True)
            (target / "boot/grub/grub.cfg").write_text(
                "menuentry 'AnduinOS' {\n linux /boot/vmlinuz-6.14-test\n}\n"
            )
            bios = target / "boot/grub/i386-pc"
            bios.mkdir()
            (bios / "normal.mod").touch()
            fallback = target / "boot/efi/EFI/BOOT/BOOTX64.EFI"
            fallback.parent.mkdir(parents=True)
            write_pe(fallback, 0x8664)
            runner.outputs[
                ("chroot", str(target), "dpkg", "--print-architecture")
            ] = ("amd64\n", "", 0)
            step.verify(context)

    def test_rejects_kernel_without_matching_initramfs(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                {"target": target, "target_efi_mounted": True},
            )
            step = InstallBootloaderStep(runner)
            step.execute(context)
            (target / "boot/vmlinuz-6.14-test").touch()
            with self.assertRaisesRegex(RuntimeError, "matching initramfs"):
                step.verify(context)

    def test_rejects_wrong_efi_machine(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                {"target": target, "target_efi_mounted": True},
            )
            step = InstallBootloaderStep(runner)
            step.execute(context)
            (target / "boot/vmlinuz-test").touch()
            (target / "boot/initrd.img-test").touch()
            (target / "boot/grub").mkdir(exist_ok=True)
            (target / "boot/grub/grub.cfg").write_text(
                "menuentry 'AnduinOS' { linux /boot/vmlinuz-test }\n"
            )
            bios = target / "boot/grub/i386-pc"
            bios.mkdir()
            (bios / "normal.mod").touch()
            fallback = target / "boot/efi/EFI/BOOT/BOOTX64.EFI"
            fallback.parent.mkdir(parents=True)
            write_pe(fallback, 0xAA64)
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                step.verify(context)

    def test_rejects_unsupported_option_before_bootloader_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target)
            runner = FakeRunner()
            runner.outputs[
                ("chroot", str(target), "grub-install", "--help")
            ] = ("--target --recheck\n", "", 0)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                {"target": target, "target_efi_mounted": True},
            )

            with self.assertRaisesRegex(
                RuntimeError, "does not support planned option"
            ):
                InstallBootloaderStep(runner).execute(context)

            self.assertEqual(
                [command for command, _kwargs in runner.commands],
                [("chroot", str(target), "grub-install", "--help")],
            )

    def test_guided_boot_preserves_shared_esp_and_verifies_nvram(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target)
            microsoft = target / "boot/efi/EFI/Microsoft/Boot/bootmgfw.efi"
            fallback = target / "boot/efi/EFI/BOOT/BOOTX64.EFI"
            microsoft.parent.mkdir(parents=True)
            fallback.parent.mkdir(parents=True)
            microsoft.write_bytes(b"windows-sentinel")
            fallback.write_bytes(b"fallback-sentinel")

            plan, inventory = guided_plan()
            inspection = replace(
                healthy_esp(plan, inventory),
                preserved_entries=capture_preserved_esp_tree(
                    target / "boot/efi"
                ),
            )
            execution = build_guided_coexistence_execution_plan(
                plan,
                inventory,
                esp_inspection=inspection,
                nvram_inspection=NvramInspection(True),
                target=str(target),
            )
            runner = self.compatible_runner(target)
            esp_device = execution.commands.devices["efi-system"]
            runner.outputs[
                ("blkid", "-s", "PARTUUID", "-o", "value", esp_device)
            ] = ("part-1\n", "", 0)
            nvram = (
                "Boot0001* Windows Boot Manager "
                "HD(1,GPT,part-1,0x800,0x100000)/"
                "File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n"
                "Boot0007* AnduinOS "
                "HD(1,GPT,part-1,0x800,0x100000)/"
                "File(\\EFI\\AnduinOS\\shimx64.efi)\n"
            )
            runner.outputs[execution.boot_commands.nvram_verify] = (
                nvram,
                "",
                0,
            )
            runner.outputs[
                ("chroot", str(target), "dpkg", "--print-architecture")
            ] = ("amd64\n", "", 0)
            logs = []
            context = InstallContext(
                plan,
                logs.append,
                values={
                    "target": target,
                    "target_efi_mounted": True,
                    "partition_devices": execution.commands.devices,
                    "guided_storage_execution_plan": execution,
                    "guided_esp_inspection": inspection,
                },
                execution_policy=ExecutionPolicy.GUIDED_DESTRUCTIVE_TEST,
            )
            step = InstallBootloaderStep(runner)
            step.preflight(context)
            step.execute(context)

            (target / "boot/vmlinuz-test").touch()
            (target / "boot/initrd.img-test").touch()
            (target / "boot/grub").mkdir(exist_ok=True)
            (target / "boot/grub/grub.cfg").write_text(
                "menuentry 'AnduinOS' { linux /boot/vmlinuz-test }\n"
            )
            vendor = target / "boot/efi/EFI/AnduinOS/shimx64.efi"
            vendor.parent.mkdir(parents=True)
            write_pe(vendor, 0x8664)
            step.verify(context)

            commands = [item[0] for item in runner.commands]
            self.assertIn(execution.boot_commands.nvram_create, commands)
            self.assertFalse(
                any(
                    "i386-pc" in argument
                    for command in commands
                    for argument in command
                )
            )
            self.assertEqual(microsoft.read_bytes(), b"windows-sentinel")
            self.assertEqual(fallback.read_bytes(), b"fallback-sentinel")
            for boundary in ("guided-boot-files", "guided-nvram"):
                before = f"[anduinos-boundary:{boundary}:before]"
                after = f"[anduinos-boundary:{boundary}:after]"
                self.assertIn(before, logs)
                self.assertIn(after, logs)
                self.assertLess(logs.index(before), logs.index(after))

            fallback.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "outside EFI/AnduinOS"):
                step.verify(context)
