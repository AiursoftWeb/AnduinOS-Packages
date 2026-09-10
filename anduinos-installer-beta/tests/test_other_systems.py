import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from fakes import FakeRunner
from helpers import valid_inventory, valid_plan
from installer_core.model import Architecture, DiskIdentity
from installer_core.other_systems import (
    WINDOWS_GRUB_SCRIPT,
    WINDOWS_LOADER_RELATIVE,
    CheckOtherDiskSystemsStep,
    WindowsBootloader,
    build_windows_grub_script,
    discover_windows_bootloaders,
)
from installer_core.steps import InstallContext, StepSkipped
from installer_core.storage_inventory import StorageInventory
from test_coexistence import windows_disk


def write_pe(path: Path, machine: int) -> None:
    data = bytearray(70)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (64).to_bytes(4, "little")
    data[64:68] = b"PE\0\0"
    data[68:70] = machine.to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def external_windows_disk():
    disk = windows_disk()
    identity = replace(
        disk.identity,
        path="/dev/nvme1n1",
        stable_id="serial:external-windows",
    )
    partitions = tuple(
        replace(
            partition,
            identity=replace(
                partition.identity,
                path=f"/dev/nvme1n1p{partition.identity.number}",
            ),
            parent_disk_id=identity.stable_id,
            filesystem_uuid=(
                "ABCD-1234"
                if partition.identity.number == 1
                else partition.filesystem_uuid
            ),
        )
        for partition in disk.partitions
    )
    return replace(disk, identity=identity, partitions=partitions)


class EspMountRunner(FakeRunner):
    def __init__(self, *, machine=0x8664):
        super().__init__()
        self.machine = machine

    def run(self, command, **kwargs):
        command = tuple(command)
        if command and command[0] == "mount":
            write_pe(
                Path(command[-1]) / WINDOWS_LOADER_RELATIVE,
                self.machine,
            )
        return super().run(command, **kwargs)


class OtherSystemDiscoveryTests(unittest.TestCase):
    def test_discovers_windows_loader_on_external_esp_read_only(self):
        plan = valid_plan()
        inventory = StorageInventory(
            (
                valid_inventory(plan).disks[0],
                external_windows_disk(),
            ),
            "inventory",
        )
        runner = EspMountRunner()
        logs = []
        with tempfile.TemporaryDirectory() as directory:
            found = discover_windows_bootloaders(
                inventory,
                target_disk_id=plan.storage.disk.stable_id,
                architecture=Architecture.AMD64,
                runner=runner,
                log=logs.append,
                scratch_root=Path(directory),
            )

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].filesystem_uuid, "ABCD-1234")
        self.assertEqual(found[0].partition_path, "/dev/nvme1n1p1")
        commands = [command for command, _kwargs in runner.commands]
        mount = commands[0]
        self.assertEqual(
            mount[:7],
            (
                "mount",
                "--read-only",
                "--types",
                "vfat",
                "--options",
                "nosuid,nodev,noexec",
                "/dev/nvme1n1p1",
            ),
        )
        self.assertEqual(commands[1][0], "umount")

    def test_rejects_loader_for_another_architecture(self):
        plan = valid_plan()
        inventory = StorageInventory(
            (external_windows_disk(),), "inventory"
        )
        runner = EspMountRunner(machine=0xAA64)
        logs = []
        with tempfile.TemporaryDirectory() as directory:
            found = discover_windows_bootloaders(
                inventory,
                target_disk_id=plan.storage.disk.stable_id,
                architecture=Architecture.AMD64,
                runner=runner,
                log=logs.append,
                scratch_root=Path(directory),
            )

        self.assertEqual(found, ())
        self.assertIn("another architecture", "\n".join(logs))

    def test_shared_target_esp_is_read_without_remounting_or_writing(self):
        for scenario in ("valid", "missing", "wrong-architecture", "wrong-device", "wrong-disk", "wrong-mount"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                esp = Path(directory) / "target/boot/efi"
                esp.mkdir(parents=True)
                if scenario != "missing":
                    write_pe(esp / WINDOWS_LOADER_RELATIVE,
                             0xAA64 if scenario == "wrong-architecture" else 0x8664)
                loader = esp / WINDOWS_LOADER_RELATIVE
                before = loader.read_bytes() if loader.exists() else None
                disk = external_windows_disk()
                partition = replace(disk.partitions[0], mountpoints=(str(esp),))
                # An ESP alone is sufficient; Windows data may be encrypted.
                disk = replace(disk, partitions=(partition,))
                runner = FakeRunner()
                found = discover_windows_bootloaders(
                    StorageInventory((disk,), "inventory"),
                    target_disk_id="other" if scenario == "wrong-disk" else disk.identity.stable_id,
                    target_esp=esp / "wrong" if scenario == "wrong-mount" else esp,
                    target_esp_device="/dev/other" if scenario == "wrong-device" else partition.identity.path,
                    architecture=Architecture.AMD64,
                    runner=runner, log=lambda message: None,
                    scratch_root=Path(directory) / "scratch",
                )
                self.assertEqual(len(found), 1 if scenario == "valid" else 0)
                self.assertEqual(loader.read_bytes() if loader.exists() else None, before)
                self.assertEqual(runner.commands, [])
                if found:
                    self.assertIn("--set=root ABCD-1234", build_windows_grub_script(found))

    def test_target_disk_separate_unmounted_esp_is_scanned_read_only(self):
        disk = external_windows_disk()
        runner = EspMountRunner()
        with tempfile.TemporaryDirectory() as directory:
            found = discover_windows_bootloaders(
                StorageInventory((disk,), "inventory"),
                target_disk_id=disk.identity.stable_id,
                target_esp=Path(directory) / "target/boot/efi",
                target_esp_device="/dev/nvme1n1p5",
                architecture=Architecture.AMD64,
                runner=runner, log=lambda message: None,
                scratch_root=Path(directory) / "scratch",
            )
        self.assertEqual(len(found), 1)
        self.assertIn("--read-only", runner.commands[0][0])
        self.assertEqual(runner.commands[-1][0][0], "umount")

    def test_same_and_other_disk_duplicate_uuids_are_rejected(self):
        disk = external_windows_disk()
        other = replace(disk, identity=replace(disk.identity, stable_id="serial:other"))
        runner = EspMountRunner()
        with tempfile.TemporaryDirectory() as directory:
            found = discover_windows_bootloaders(
                StorageInventory((disk, other), "inventory"),
                target_disk_id=disk.identity.stable_id,
                architecture=Architecture.AMD64,
                runner=runner, log=lambda message: None,
                scratch_root=Path(directory),
            )
        self.assertEqual(found, ())

    def test_generated_entry_is_stable_and_chainloads_windows(self):
        entry = WindowsBootloader(
            disk_stable_id="serial:windows",
            partition_path="/dev/nvme1n1p1",
            partuuid="part-windows",
            filesystem_uuid="ABCD-1234",
        )

        script = build_windows_grub_script((entry,))

        self.assertIn("menuentry 'Windows Boot Manager'", script)
        self.assertIn("--class windows --class os", script)
        self.assertIn("set timeout_style=menu", script)
        self.assertIn("set timeout=10", script)
        self.assertIn(
            "search --no-floppy --fs-uuid --set=root ABCD-1234",
            script,
        )
        self.assertIn(
            "chainloader /EFI/Microsoft/Boot/bootmgfw.efi", script
        )
        self.assertNotIn("/dev/nvme1n1p1", script)


class CheckOtherDiskSystemsStepTests(unittest.TestCase):
    def entry(self):
        return WindowsBootloader(
            disk_stable_id="serial:windows",
            partition_path="/dev/nvme1n1p1",
            partuuid="part-windows",
            filesystem_uuid="ABCD-1234",
        )

    def test_writes_only_target_grub_source_and_regenerates_menu(self):
        runner = FakeRunner()
        entry = self.entry()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "boot/grub").mkdir(parents=True)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                values={
                    "target": target,
                    "chroot_environment_ready": True,
                    "partition_devices": {"efi-system": "/dev/sda1"},
                },
            )
            probe = Mock(return_value=(entry,))
            step = CheckOtherDiskSystemsStep(
                runner,
                inventory_probe=lambda: StorageInventory((), "inventory"),
                windows_probe=probe,
            )
            step.preflight(context)
            step.execute(context)
            self.assertEqual(probe.call_args.kwargs["target_esp"], target / "boot/efi")
            self.assertEqual(probe.call_args.kwargs["target_esp_device"], "/dev/sda1")
            script_path = target / WINDOWS_GRUB_SCRIPT
            script = script_path.read_text(encoding="utf-8")
            (target / "boot/grub/grub.cfg").write_text(
                "menuentry 'AnduinOS' {}\n"
                "# AnduinOS Windows entry: ABCD-1234\n",
                encoding="utf-8",
            )
            step.verify(context)

            self.assertEqual(script_path.stat().st_mode & 0o777, 0o755)
            self.assertIn("Windows Boot Manager", script)

        commands = [command for command, _kwargs in runner.commands]
        self.assertEqual(
            commands[-1], ("chroot", str(target), "update-grub")
        )
        self.assertEqual(runner.required, ["mount", "umount", "chroot"])

    def test_no_windows_is_an_expected_skip(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = InstallContext(
                valid_plan(),
                lambda _message: None,
                values={
                    "target": target,
                    "chroot_environment_ready": True,
                },
            )
            step = CheckOtherDiskSystemsStep(
                runner,
                inventory_probe=lambda: StorageInventory((), "inventory"),
                windows_probe=lambda *_args, **_kwargs: (),
            )

            with self.assertRaisesRegex(StepSkipped, "No UEFI Windows"):
                step.execute(context)

        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()
