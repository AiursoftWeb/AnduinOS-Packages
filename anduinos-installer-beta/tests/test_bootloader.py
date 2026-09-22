import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.bootloader import (
    InstallBootloaderStep,
    _deploy_portable_fallback,
    _ensure_vendor_nvram_first,
    _portable_fallback_paths,
    _verify_signed_image,
    _verify_portable_fallback,
    _verify_grub_filesystem_modules,
)
from installer_core.boot_commands import build_boot_commands
from installer_core.esp import (
    NvramInspection,
    capture_preserved_esp_tree,
)
from installer_core.storage_planning import (
    build_guided_coexistence_execution_plan,
    build_manual_storage_execution_plan,
)
from installer_core.model import Architecture, Filesystem
from installer_core.steps import InstallContext
from installer_core.validation import ExecutionPolicy
from test_guided_storage_graph import guided_plan
from test_guided_storage_planning import healthy_esp
from test_manual_storage_graph import manual_plan
from test_manual_storage_planning import healthy_esp as healthy_manual_esp


def prepare_target(target: Path) -> None:
    for executable in (
        "usr/sbin/grub-install",
        "usr/sbin/update-grub",
        "usr/bin/dracut",
        "usr/bin/lsinitrd",
    ):
        path = target / executable
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    for platform in ("i386-pc", "x86_64-efi", "arm64-efi"):
        module_info = target / f"usr/lib/grub/{platform}/modinfo.sh"
        module_info.parent.mkdir(parents=True, exist_ok=True)
        module_info.touch()
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


def erase_context_values(target: Path) -> dict[str, object]:
    return {
        "target": target,
        "target_efi_mounted": True,
        "partition_devices": {"efi-system": "/dev/nvme0n1p2"},
    }


class InstallBootloaderTests(unittest.TestCase):
    def test_portable_signature_check_rejects_a_bad_authenticode_digest(self):
        class SignatureRunner(FakeRunner):
            def run(self, command, **kwargs):
                command = tuple(command)
                self.commands.append((command, kwargs))
                if command[:3] == ("openssl", "pkcs7", "-inform"):
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        "-----BEGIN CERTIFICATE-----\n"
                        "test\n"
                        "-----END CERTIFICATE-----\n",
                        "",
                    )
                if command[:2] == ("sbverify", "--cert"):
                    return subprocess.CompletedProcess(command, 1, "", "bad digest")
                return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "shimx64.efi"
            write_pe(image, 0x8664)
            with self.assertRaisesRegex(RuntimeError, "signature is invalid"):
                _verify_signed_image(SignatureRunner(), image, 0x8664)

    def test_direct_portable_chain_excludes_nvram_registrar_and_matches_vendor(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            vendor = target / "boot/efi/EFI/AnduinOS"
            vendor.mkdir(parents=True)
            for name in ("shimx64.efi", "grubx64.efi", "mmx64.efi"):
                write_pe(vendor / name, 0x8664)
                with (vendor / name).open("ab") as stream:
                    stream.write(name.encode())
            (vendor / "grub.cfg").write_text(
                "search --fs-uuid root; configfile /boot/grub/grub.cfg",
                encoding="utf-8",
            )
            runner = FakeRunner()
            plan = valid_plan(external_target=True)

            with patch(
                "installer_core.bootloader._verify_signed_image"
            ) as verify_signed:
                _deploy_portable_fallback(runner, target, plan)
                _verify_portable_fallback(runner, target, plan)

            fallback = target / "boot/efi/EFI/BOOT"
            self.assertEqual(
                (fallback / "BOOTX64.EFI").read_bytes(),
                (vendor / "shimx64.efi").read_bytes(),
            )
            self.assertEqual(
                (fallback / "grubx64.efi").read_bytes(),
                (vendor / "grubx64.efi").read_bytes(),
            )
            self.assertEqual(
                (fallback / "mmx64.efi").read_bytes(),
                (vendor / "mmx64.efi").read_bytes(),
            )
            self.assertEqual(
                (fallback / "grub.cfg").read_bytes(),
                (vendor / "grub.cfg").read_bytes(),
            )
            self.assertFalse((fallback / "fbx64.efi").exists())
            # Three staged images, then three deployed images per verification.
            self.assertEqual(verify_signed.call_count, 9)

            (fallback / "grubx64.efi").write_bytes(b"tampered")
            with self.assertRaisesRegex(
                RuntimeError, "does not match vendor payload"
            ):
                _verify_portable_fallback(runner, target, plan)

    def test_portable_chain_rejects_internal_disk_plan_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = FakeRunner()
            with self.assertRaisesRegex(RuntimeError, "not authorized"):
                _deploy_portable_fallback(runner, target, valid_plan())
            self.assertFalse((target / "boot/efi/EFI/BOOT").exists())
            self.assertEqual(runner.commands, [])

    def test_arm64_portable_chain_uses_standard_filenames(self):
        plan = valid_plan(
            architecture=Architecture.ARM64,
            external_target=True,
        )
        pairs = _portable_fallback_paths(Path("/target"), plan)
        self.assertEqual(
            tuple((source.name, destination.name) for source, destination in pairs),
            (
                ("grubaa64.efi", "grubaa64.efi"),
                ("mmaa64.efi", "mmaa64.efi"),
                ("grub.cfg", "grub.cfg"),
                ("shimaa64.efi", "BOOTAA64.EFI"),
            ),
        )

    def test_portable_fallback_tools_are_required_only_when_written(self):
        external = InstallBootloaderStep(FakeRunner())
        external_context = InstallContext(
            valid_plan(external_target=True), lambda _message: None
        )
        external.preflight(external_context)
        self.assertEqual(
            external.runner.required, ["openssl", "sbattach", "sbverify"]
        )

        internal = InstallBootloaderStep(FakeRunner())
        internal.preflight(InstallContext(valid_plan(), lambda _message: None))
        self.assertEqual(internal.runner.required, [])

    def compatible_runner(self, target: Path) -> FakeRunner:
        runner = FakeRunner()
        runner.outputs[
            ("chroot", str(target), "grub-install", "--help")
        ] = (GRUB_INSTALL_HELP, "", 0)
        runner.outputs[
            ("blkid", "-s", "PARTUUID", "-o", "value", "/dev/nvme0n1p2")
        ] = ("part-2\n", "", 0)
        runner.outputs[("efibootmgr", "--verbose")] = (
            "BootOrder: 0007,0001\n"
            "Boot0007* AnduinOS "
            "HD(2,GPT,part-2,0x1800,0x200000)/"
            "File(\\EFI\\AnduinOS\\shimx64.efi)\n"
            "Boot0001* UEFI OS "
            "HD(2,GPT,part-2,0x1800,0x200000)/"
            "File(\\EFI\\BOOT\\BOOTX64.EFI)\n",
            "",
            0,
        )
        return runner

    def test_external_execute_publishes_fallback_and_keeps_vendor_nvram(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target)
            vendor = target / "boot/efi/EFI/AnduinOS"
            vendor.mkdir(parents=True)
            for name in ("shimx64.efi", "grubx64.efi", "mmx64.efi"):
                write_pe(vendor / name, 0x8664)
            (vendor / "grub.cfg").write_text(
                "configfile /boot/grub/grub.cfg\n", encoding="utf-8"
            )
            runner = self.compatible_runner(target)
            context = InstallContext(
                valid_plan(external_target=True),
                lambda _message: None,
                erase_context_values(target),
            )

            with patch("installer_core.bootloader._verify_signed_image"):
                InstallBootloaderStep(runner).execute(context)

            fallback = target / "boot/efi/EFI/BOOT"
            self.assertTrue((fallback / "BOOTX64.EFI").is_file())
            self.assertFalse((fallback / "fbx64.efi").exists())
            commands = [item[0] for item in runner.commands]
            self.assertLess(
                commands.index(("chroot", str(target), "update-grub")),
                next(
                    index
                    for index, command in enumerate(commands)
                    if command[:2] == ("efibootmgr", "--create")
                ),
            )

    def test_moves_vendor_entry_to_the_front_of_boot_order(self):
        class ReorderingRunner(FakeRunner):
            def run(self, command, **kwargs):
                command = tuple(command)
                result = super().run(command, **kwargs)
                if command == ("efibootmgr", "--bootorder", "0007,0001"):
                    self.outputs[("efibootmgr", "--verbose")] = (
                        self.outputs[("efibootmgr", "--verbose")][0].replace(
                            "BootOrder: 0001,0007",
                            "BootOrder: 0007,0001",
                        ),
                        "",
                        0,
                    )
                return result

        runner = ReorderingRunner()
        runner.outputs[
            ("blkid", "-s", "PARTUUID", "-o", "value", "/dev/nvme0n1p2")
        ] = ("part-2\n", "", 0)
        runner.outputs[("efibootmgr", "--verbose")] = (
            "BootOrder: 0001,0007\n"
            "Boot0001* UEFI OS "
            "HD(2,GPT,part-2,0x1800,0x200000)/"
            "\\EFI\\BOOT\\BOOTX64.EFI\n"
            "Boot0007* AnduinOS "
            "HD(2,GPT,part-2,0x1800,0x200000)/"
            "\\EFI\\AnduinOS\\shimx64.efi\n",
            "",
            0,
        )
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            erase_context_values(Path("/target")),
        )

        _ensure_vendor_nvram_first(
            runner,
            context,
            build_boot_commands(context.plan, "/target"),
        )

        self.assertIn(
            (("efibootmgr", "--bootorder", "0007,0001"), {"timeout": 30}),
            runner.commands,
        )

    def test_runs_dracut_before_grub_install_and_update_grub_last(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                erase_context_values(target),
            )
            step = InstallBootloaderStep(runner)
            step.preflight(context)
            step.execute(context)

        commands = [item[0] for item in runner.commands]
        self.assertEqual(commands[0][2:], ("grub-install", "--help"))
        self.assertFalse(runner.commands[0][1]["log_output"])
        self.assertEqual(commands[1][2], "dracut")
        self.assertIn("--no-hostonly-cmdline", commands[1])
        self.assertIn("--regenerate-all", commands[1])
        self.assertIn(
            ("chroot", str(target), "update-grub"),
            commands,
        )
        self.assertEqual(commands[-1], ("efibootmgr", "--verbose"))
        self.assertEqual(
            [
                command[3]
                for command in commands
                if len(command) > 3
                and command[2] == "grub-install"
                and command[3].startswith("--target=")
            ],
            ["--target=i386-pc", "--target=x86_64-efi"],
        )

    def test_advanced_root_filesystems_require_grub_and_initrd_drivers(self):
        cases = {
            Filesystem.XFS: "kernel/fs/xfs/xfs.ko.zst",
            Filesystem.F2FS: "kernel/fs/f2fs/f2fs.ko.zst",
        }
        for filesystem, initrd_module in cases.items():
            with self.subTest(filesystem=filesystem.value):
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory)
                    prepare_target(target)
                    grub_module = (
                        target
                        / "usr/lib/grub/x86_64-efi"
                        / f"{filesystem.value}.mod"
                    )
                    grub_module.touch()
                    plan, inventory = manual_plan(filesystem=filesystem)
                    execution = build_manual_storage_execution_plan(
                        plan,
                        inventory,
                        esp_inspection=healthy_manual_esp(inventory),
                        nvram_inspection=NvramInspection(True),
                        target=str(target),
                    )
                    runner = self.compatible_runner(target)
                    esp = execution.commands.devices["efi-system"]
                    runner.outputs[
                        ("blkid", "-s", "PARTUUID", "-o", "value", esp)
                    ] = ("part-1\n", "", 0)
                    runner.outputs[execution.boot_commands.nvram_verify] = (
                        "BootOrder: 0007\n"
                        "Boot0007* AnduinOS "
                        "HD(1,GPT,part-1,0x800,0x100000)/"
                        "File(\\EFI\\AnduinOS\\shimx64.efi)\n",
                        "",
                        0,
                    )
                    context = InstallContext(
                        plan,
                        lambda _message: None,
                        values={
                            "target": target,
                            "target_efi_mounted": True,
                            "partition_devices": execution.commands.devices,
                            "manual_storage_execution_plan": execution,
                        },
                    )
                    step = InstallBootloaderStep(runner)
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
                    runner.outputs[
                        ("chroot", str(target), "dpkg", "--print-architecture")
                    ] = ("amd64\n", "", 0)
                    runner.outputs[
                        (
                            "chroot",
                            str(target),
                            "lsinitrd",
                            "-m",
                            "/boot/initrd.img-test",
                        )
                    ] = ("rootfs-block\n", "", 0)
                    initrd_command = (
                        "chroot",
                        str(target),
                        "lsinitrd",
                        "/boot/initrd.img-test",
                    )
                    runner.outputs[initrd_command] = (
                        initrd_module + "\n",
                        "",
                        0,
                    )
                    step.verify(context)

                    runner.outputs[initrd_command] = ("", "", 0)
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "missing its root filesystem driver",
                    ):
                        step.verify(context)

    def test_missing_advanced_grub_filesystem_module_is_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target)
            installs = (
                (
                    "chroot",
                    str(target),
                    "grub-install",
                    "--target=x86_64-efi",
                ),
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "GRUB xfs modules are missing",
            ):
                _verify_grub_filesystem_modules(
                    target,
                    installs,
                    Filesystem.XFS,
                )

    def test_verifies_matching_kernel_grub_bios_and_efi_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                erase_context_values(target),
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
            vendor = target / "boot/efi/EFI/AnduinOS/shimx64.efi"
            vendor.parent.mkdir(parents=True)
            write_pe(vendor, 0x8664)
            runner.outputs[
                ("chroot", str(target), "dpkg", "--print-architecture")
            ] = ("amd64\n", "", 0)
            runner.outputs[
                ("chroot", str(target), "lsinitrd", "-m", "/boot/initrd.img-6.14-test")
            ] = ("anduinos-btrfs-snapshots-manager\n", "", 0)
            step.verify(context)

    def test_rejects_kernel_without_matching_initramfs(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                erase_context_values(target),
            )
            step = InstallBootloaderStep(runner)
            step.execute(context)
            (target / "boot/vmlinuz-6.14-test").touch()
            with self.assertRaisesRegex(RuntimeError, "matching Dracut initrd"):
                step.verify(context)

    def test_rejects_wrong_efi_machine(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            runner = self.compatible_runner(target)
            prepare_target(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                erase_context_values(target),
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
            vendor = target / "boot/efi/EFI/AnduinOS/shimx64.efi"
            vendor.parent.mkdir(parents=True)
            write_pe(vendor, 0xAA64)
            runner.outputs[
                ("chroot", str(target), "lsinitrd", "-m", "/boot/initrd.img-test")
            ] = ("anduinos-btrfs-snapshots-manager\n", "", 0)
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
                erase_context_values(target),
            )

            with self.assertRaisesRegex(
                RuntimeError, "does not support planned option"
            ):
                InstallBootloaderStep(runner).execute(context)

            self.assertEqual(
                [command for command, _kwargs in runner.commands],
                [("chroot", str(target), "grub-install", "--help")],
            )

    def test_rejects_missing_platform_modules_before_running_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            prepare_target(target)
            (target / "usr/lib/grub/i386-pc/modinfo.sh").unlink()
            runner = self.compatible_runner(target)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                erase_context_values(target),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                r"GRUB platform modules.*i386-pc/modinfo\.sh",
            ):
                InstallBootloaderStep(runner).execute(context)

            self.assertEqual(runner.commands, [])

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
                "BootOrder: 0007,0001\n"
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
            runner.outputs[
                ("chroot", str(target), "lsinitrd", "-m", "/boot/initrd.img-test")
            ] = ("anduinos-btrfs-snapshots-manager\n", "", 0)
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
