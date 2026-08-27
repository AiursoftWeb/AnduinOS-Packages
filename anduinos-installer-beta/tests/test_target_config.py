import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.steps import InstallContext
from installer_core.target_config import ConfigureStorageStep
from installer_core.model import Filesystem


class ConfigureStorageTests(unittest.TestCase):
    def test_writes_btrfs_efi_disk_swap_and_zram_config(self):
        plan = valid_plan()
        runner = FakeRunner()
        devices = {
            "root": "/dev/nvme0n1p4",
            "efi-system": "/dev/nvme0n1p2",
            "swap": "/dev/nvme0n1p3",
        }
        for name, device in devices.items():
            runner.outputs[
                ("blkid", "-s", "UUID", "-o", "value", device)
            ] = (f"{name}-uuid\n", "", 0)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = InstallContext(
                plan,
                lambda _message: None,
                values={"target": target, "partition_devices": devices},
            )
            step = ConfigureStorageStep(runner)
            step.execute(context)
            step.verify(context)
            fstab = (target / "etc/fstab").read_text()
            zram = (target / "etc/default/anduinos-zram").read_text()

        self.assertIn(
            "UUID=root-uuid / btrfs "
            "defaults,subvol=@root,compress=zstd,noatime 0 0",
            fstab,
        )
        for mount_point, name in (
            ("/home", "@home"),
            ("/var/log", "@log"),
            ("/.snapshots", "@snapshots"),
            ("/var/lib/containers", "@containers"),
            ("/var/lib/libvirt/images", "@libvirt"),
        ):
            self.assertIn(
                f" {mount_point} btrfs "
                f"defaults,subvol={name},compress=zstd,noatime 0 0",
                fstab,
            )
        self.assertIn(
            "UUID=swap-uuid none swap sw,pri=10 0 0",
            fstab,
        )
        self.assertIn("ZRAM_ALGORITHM=lz4", zram)
        self.assertIn("ZRAM_PRIORITY=100", zram)

    def test_ext4_has_one_root_mount_and_no_subvolumes(self):
        base = valid_plan()
        plan = replace(
            base,
            storage=replace(base.storage, filesystem=Filesystem.EXT4),
        )
        runner = FakeRunner()
        devices = {
            "root": "/dev/root",
            "efi-system": "/dev/efi",
            "swap": "/dev/swap",
        }
        for name, device in devices.items():
            runner.outputs[
                ("blkid", "-s", "UUID", "-o", "value", device)
            ] = (f"{name}-uuid\n", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = InstallContext(
                plan,
                lambda _message: None,
                values={"target": target, "partition_devices": devices},
            )
            ConfigureStorageStep(runner).execute(context)
            fstab = (target / "etc/fstab").read_text()
        self.assertIn(
            "UUID=root-uuid / ext4 defaults,noatime 0 1", fstab
        )
        self.assertNotIn("subvol=", fstab)

    def test_missing_uuid_is_fatal(self):
        plan = valid_plan()
        runner = FakeRunner()
        devices = {
            "root": "/dev/root",
            "efi-system": "/dev/efi",
            "swap": "/dev/swap",
        }
        with tempfile.TemporaryDirectory() as directory:
            context = InstallContext(
                plan,
                lambda _message: None,
                values={"target": Path(directory), "partition_devices": devices},
            )
            with self.assertRaisesRegex(RuntimeError, "Missing filesystem UUID"):
                ConfigureStorageStep(runner).execute(context)

    def test_zero_disk_swap_keeps_zram_and_omits_fstab_swap(self):
        plan = valid_plan(swap_size_mib=0)
        runner = FakeRunner()
        devices = {"root": "/dev/root", "efi-system": "/dev/efi"}
        for name, device in devices.items():
            runner.outputs[
                ("blkid", "-s", "UUID", "-o", "value", device)
            ] = (f"{name}-uuid\n", "", 0)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = InstallContext(
                plan,
                lambda _message: None,
                values={"target": target, "partition_devices": devices},
            )
            step = ConfigureStorageStep(runner)
            step.execute(context)
            step.verify(context)
            fstab = (target / "etc/fstab").read_text()
            zram = (target / "etc/default/anduinos-zram").read_text()
        self.assertNotIn(" none swap ", fstab)
        self.assertIn("ZRAM_ENABLED=yes", zram)
        self.assertIn("ZRAM_PRIORITY=100", zram)
