import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.steps import InstallContext
from installer_core.storage_steps import MountTargetStep, PrepareStorageStep


class PrepareStorageStepTests(unittest.TestCase):
    def test_executes_partitioning_before_formatting(self):
        plan = valid_plan()
        runner = FakeRunner()
        context = InstallContext(plan, lambda _message: None)
        step = PrepareStorageStep(runner)

        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            step.execute(context)

        argv = [item[0] for item in runner.commands]
        parted_index = next(i for i, cmd in enumerate(argv) if cmd[0] == "parted")
        btrfs_index = next(
            i for i, cmd in enumerate(argv) if cmd[0] == "mkfs.btrfs"
        )
        self.assertLess(parted_index, btrfs_index)
        self.assertEqual(
            context.values["partition_devices"]["swap"], "/dev/nvme0n1p3"
        )

    def test_preflight_requires_filesystem_tools(self):
        plan = valid_plan()
        runner = FakeRunner()
        PrepareStorageStep(runner).preflight(
            InstallContext(plan, lambda _message: None)
        )
        self.assertIn("mkfs.btrfs", runner.required)
        self.assertIn("mkswap", runner.required)


class MountTargetStepTests(unittest.TestCase):
    def test_btrfs_mounts_complete_subvolume_abi_and_efi(self):
        plan = valid_plan()
        runner = FakeRunner()
        context = InstallContext(
            plan,
            lambda _message: None,
            values={
                "partition_devices": {
                    "root": "/dev/nvme0n1p4",
                    "efi-system": "/dev/nvme0n1p2",
                }
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            MountTargetStep(runner, target=target).execute(context)

        commands = [item[0] for item in runner.commands]
        expected = {
            "@root": target,
            "@home": target / "home",
            "@log": target / "var/log",
            "@snapshots": target / ".snapshots",
            "@containers": target / "var/lib/containers",
            "@libvirt": target / "var/lib/libvirt/images",
        }
        for name, mount_path in expected.items():
            self.assertIn(
                (
                    "btrfs",
                    "subvolume",
                    "create",
                    str(target / name),
                ),
                commands,
            )
            self.assertIn(
                (
                    "mount",
                    "-o",
                    f"subvol={name},compress=zstd,noatime",
                    "/dev/nvme0n1p4",
                    str(mount_path),
                ),
                commands,
            )
        self.assertIn(
            ("mount", "/dev/nvme0n1p2", str(target / "boot/efi")),
            commands,
        )

    def test_btrfs_cleanup_unmounts_deepest_mounts_before_root(self):
        runner = FakeRunner()
        target = Path("/target-test")
        mounts = [
            target,
            target / "home",
            target / "var/log",
            target / ".snapshots",
            target / "var/lib/containers",
            target / "var/lib/libvirt/images",
        ]
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            values={
                "target_efi_mounted": True,
                "target_btrfs_mounts": mounts,
            },
        )
        MountTargetStep(runner, target=target).cleanup(context)
        commands = [item[0] for item in runner.commands]
        self.assertEqual(commands[0], ("umount", "/target-test/boot/efi"))
        self.assertEqual(
            commands[1:],
            [("umount", str(path)) for path in reversed(mounts)],
        )

    def test_cleanup_unmounts_efi_before_root(self):
        plan = valid_plan()
        runner = FakeRunner()
        context = InstallContext(
            plan,
            lambda _message: None,
            values={
                "target_efi_mounted": True,
                "target_root_mounted": True,
            },
        )
        target = Path("/target-test")
        MountTargetStep(runner, target=target).cleanup(context)
        commands = [item[0] for item in runner.commands]
        self.assertEqual(commands[0], ("umount", "/target-test/boot/efi"))
        self.assertEqual(commands[1], ("umount", "/target-test"))
