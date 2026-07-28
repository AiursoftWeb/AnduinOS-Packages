import tempfile
import unittest
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.bootloader import InstallBootloaderStep
from installer_core.steps import InstallContext


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


class InstallBootloaderTests(unittest.TestCase):
    def test_runs_initramfs_before_grub_install_and_update_grub_last(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
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
        self.assertEqual(commands[0][2], "update-initramfs")
        self.assertEqual(commands[-1][2], "update-grub")
        self.assertEqual(
            [command[3] for command in commands if command[2] == "grub-install"],
            ["--target=i386-pc", "--target=x86_64-efi"],
        )

    def test_verifies_matching_kernel_grub_bios_and_efi_artifacts(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
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
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
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
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
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
