import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

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
                },
            )
            step = CheckOtherDiskSystemsStep(
                runner,
                inventory_probe=lambda: StorageInventory((), "inventory"),
                windows_probe=lambda *_args, **_kwargs: (entry,),
            )
            step.preflight(context)
            step.execute(context)
            script_path = target / WINDOWS_GRUB_SCRIPT
            script = script_path.read_text(encoding="utf-8")
            (target / "boot/grub/grub.cfg").write_text(
                "menuentry 'AnduinOS' {}\n"
                "# AnduinOS external Windows entry: ABCD-1234\n",
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
