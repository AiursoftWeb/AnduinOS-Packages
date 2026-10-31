import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.linux_systems import (
    LINUX_GRUB_SCRIPT, CheckLinuxSystemsStep, LinuxBootloader,
    build_linux_grub_script, discover_linux_bootloaders,
)
from installer_core.model import Architecture
from installer_core.steps import InstallContext, StepSkipped
from installer_core.storage_inventory import StorageInventory
from test_other_systems import external_windows_disk, write_pe


def write_chain(root, vendor, suffix="x64", machine=0x8664):
    directory = root / "EFI" / vendor
    for name in (f"shim{suffix}.efi", f"grub{suffix}.efi"):
        write_pe(directory / name, machine)
    (directory / "grub.cfg").write_text("foreign configuration\n")


class LinuxMountRunner(FakeRunner):
    def __init__(self, populate):
        super().__init__()
        self.populate = populate

    def run(self, command, **kwargs):
        if command[0] == "mount":
            self.populate(Path(command[-1]))
        return super().run(command, **kwargs)


class LinuxSystemsTests(unittest.TestCase):
    def discover(self, directory, runner, *, disk=None, **kwargs):
        disk = disk or external_windows_disk()
        return discover_linux_bootloaders(
            StorageInventory((disk,), "inventory"),
            architecture=kwargs.pop("architecture", Architecture.AMD64),
            target_disk_id=disk.identity.stable_id, target_esp_device="/dev/new1",
            runner=runner, log=lambda message: None, scratch_root=Path(directory), **kwargs,
        )

    def test_same_disk_anduinos_and_ubuntu_are_read_only(self):
        runner = LinuxMountRunner(lambda root: [write_chain(root, vendor) for vendor in ("AnduinOS", "ubuntu")])
        with tempfile.TemporaryDirectory() as directory:
            found = self.discover(directory, runner)
        self.assertEqual([item.vendor for item in found], ["AnduinOS", "ubuntu"])
        self.assertEqual(len(runner.commands), 2)
        self.assertEqual(runner.commands[0][0][:6],
                         ("mount", "--read-only", "--types", "vfat", "--options", "nosuid,nodev,noexec"))
        self.assertEqual(runner.commands[1][0][0], "umount")

    def test_shared_target_esp_only_adds_ubuntu_and_never_remounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for vendor in ("AnduinOS", "ubuntu"):
                write_chain(root, vendor)
            disk = external_windows_disk()
            part = replace(disk.partitions[0], mountpoints=(str(root),))
            disk = replace(disk, partitions=(part,))
            runner = FakeRunner()
            before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            found = discover_linux_bootloaders(
                StorageInventory((disk,), "inventory"), architecture=Architecture.AMD64,
                target_disk_id=disk.identity.stable_id, target_esp_device=part.identity.path,
                target_esp=root, runner=runner, log=lambda message: None,
            )
            self.assertEqual([item.vendor for item in found], ["ubuntu"])
            self.assertEqual(runner.commands, [])
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_duplicate_uuid_without_loader_and_unrelated_mount_are_not_searched(self):
        disk = external_windows_disk()
        esp = disk.partitions[0]
        for parts in ((esp, replace(esp, identity=replace(esp.identity, path="/dev/clone1"))),
                      (replace(esp, mountpoints=("/foreign",)),)):
            with self.subTest(parts=parts), tempfile.TemporaryDirectory() as directory:
                runner = LinuxMountRunner(lambda root: write_chain(root, "AnduinOS"))
                self.assertEqual(self.discover(directory, runner, disk=replace(disk, partitions=parts)), ())
                self.assertEqual(runner.commands, [])

    def test_incomplete_wrong_architecture_and_invalid_chains_are_ignored(self):
        def populate(root, scenario):
            write_chain(root, "AnduinOS")
            path = root / "EFI/AnduinOS"
            if scenario == "missing":
                (path / "grubx64.efi").unlink()
            elif scenario == "empty":
                (path / "grub.cfg").write_text("")
            elif scenario == "invalid":
                (path / "shimx64.efi").write_bytes(b"bad")
            else:
                write_pe(path / "shimx64.efi", 0xAA64)
        for scenario in ("missing", "empty", "invalid", "architecture"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                runner = LinuxMountRunner(lambda root: populate(root, scenario))
                self.assertEqual(self.discover(directory, runner), ())
                self.assertEqual(runner.commands[-1][0][0], "umount")

    def test_arm64_uses_aa64_shim(self):
        runner = LinuxMountRunner(lambda root: write_chain(root, "AnduinOS", "aa64", 0xAA64))
        with tempfile.TemporaryDirectory() as directory:
            found = self.discover(directory, runner, architecture=Architecture.ARM64)
        self.assertIn("chainloader /EFI/AnduinOS/shimaa64.efi", build_linux_grub_script(found))

    def test_live_media_and_unmounted_target_are_not_added_as_other_systems(self):
        disk = external_windows_disk()
        for live in (True, False):
            with self.subTest(live=live):
                runner = FakeRunner()
                inventory = StorageInventory((disk,), "inventory",
                                             (disk.identity.path,) if live else ())
                found = discover_linux_bootloaders(
                    inventory, architecture=Architecture.AMD64, runner=runner,
                    log=lambda message: None, target_disk_id=disk.identity.stable_id,
                    target_esp_device="/dev/new1" if live else disk.partitions[0].identity.path,
                )
                self.assertEqual(found, ())
                self.assertEqual(runner.commands, [])

    def test_script_survives_update_grub_without_foreign_kernel_paths(self):
        entry = LinuxBootloader("AnduinOS", "/dev/old1", "ABCD-1234", "x64")
        script = build_linux_grub_script((entry,))
        self.assertIn("if search --no-floppy --fs-uuid", script)
        self.assertIn("chainloader /EFI/AnduinOS/shimx64.efi", script)
        self.assertNotIn("/dev/old1", script)
        self.assertNotIn("initrd", script)
        self.assertNotIn("vmlinuz", script)
        result = subprocess.run(["sh"], input=script, text=True, capture_output=True, check=True)
        if shutil.which("grub-script-check"):
            subprocess.run(["grub-script-check"], input=result.stdout, text=True, capture_output=True, check=True)
        for bad in (replace(entry, vendor="$(bad)"), replace(entry, suffix="bad"),
                    replace(entry, filesystem_uuid="1234-5678;bad")):
            with self.assertRaises(ValueError):
                build_linux_grub_script((bad,))

    def test_step_only_writes_new_root_and_verifies_generated_configuration(self):
        entry = LinuxBootloader("AnduinOS", "/dev/old1", "ABCD-1234", "x64")
        runner = FakeRunner()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = InstallContext(valid_plan(), lambda message: None,
                                     values={"target": target, "chroot_environment_ready": True,
                                             "partition_devices": {"efi-system": "/dev/new1"}})
            probe = Mock(return_value=(entry,))
            step = CheckLinuxSystemsStep(runner, inventory_probe=lambda: None, linux_probe=probe)
            step.preflight(context)
            step.execute(context)
            self.assertEqual(probe.call_args.kwargs["target_esp_device"], "/dev/new1")
            script = target / LINUX_GRUB_SCRIPT
            self.assertEqual(script.stat().st_mode & 0o777, 0o755)
            cfg = target / "boot/grub/grub.cfg"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(script.read_text())
            step.verify(context)
            cfg.write_text("missing entry")
            with self.assertRaisesRegex(RuntimeError, "missing"):
                step.verify(context)
            # Stale installer-owned source is removed when no chain survives.
            probe.return_value = ()
            with self.assertRaises(StepSkipped):
                step.execute(context)
            self.assertFalse(script.exists())
            self.assertEqual([cmd for cmd, _ in runner.commands],
                             [("chroot", str(target), "update-grub")] * 2)


if __name__ == "__main__":
    unittest.main()
