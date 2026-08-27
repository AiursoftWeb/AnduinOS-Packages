import subprocess
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
        context = InstallContext(plan, lambda _message: None)
        PrepareStorageStep(runner).preflight(context)
        self.assertIn("mkfs.btrfs", runner.required)
        self.assertIn("mkswap", runner.required)
        self.assertIn("swapon", runner.required)
        self.assertIn("swapoff", runner.required)
        execution_plan = context.values["erase_disk_execution_plan"]
        self.assertIs(
            context.values["storage_write_set"],
            execution_plan.write_set,
        )

    def test_zero_disk_swap_does_not_require_or_run_mkswap(self):
        plan = valid_plan(swap_size_mib=0)
        runner = FakeRunner()
        context = InstallContext(plan, lambda _message: None)
        step = PrepareStorageStep(runner)
        step.preflight(context)
        self.assertNotIn("mkswap", runner.required)
        self.assertIn("swapon", runner.required)
        self.assertIn("swapoff", runner.required)
        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            step.execute(context)
        self.assertNotIn("swap", context.values["partition_devices"])
        self.assertFalse(
            any(
                command[0][0] == "mkswap"
                for command in runner.commands
            )
        )

    def test_execute_reuses_the_preflight_execution_plan(self):
        plan = valid_plan()
        runner = FakeRunner()
        context = InstallContext(plan, lambda _message: None)
        step = PrepareStorageStep(runner)
        step.preflight(context)
        frozen = context.values["erase_disk_execution_plan"]

        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            step.execute(context)

        self.assertIs(context.values["erase_disk_execution_plan"], frozen)
        self.assertIs(context.values["layout"], frozen.layout)

    def test_retry_deactivates_only_selected_disk_swap_before_parted(self):
        plan = valid_plan()
        runner = FakeRunner()
        runner.outputs[
            ("swapon", "--show=NAME", "--noheadings", "--raw")
        ] = (
            "/dev/nvme0n1p3\n/swapfile\n/dev/sdb3\n",
            "",
            0,
        )
        logs = []
        context = InstallContext(plan, logs.append)

        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            PrepareStorageStep(runner).execute(context)

        commands = [item[0] for item in runner.commands]
        swapoff = ("swapoff", "/dev/nvme0n1p3")
        first_parted = next(
            command for command in commands if command[0] == "parted"
        )
        self.assertIn(swapoff, commands)
        self.assertLess(commands.index(swapoff), commands.index(first_parted))
        self.assertNotIn(("swapoff", "/swapfile"), commands)
        self.assertNotIn(("swapoff", "/dev/sdb3"), commands)
        self.assertIn("earlier attempt", "\n".join(logs))

    def test_cleanup_deactivates_target_swap_and_refreshes_kernel(self):
        plan = valid_plan()
        runner = FakeRunner()
        runner.outputs[
            ("swapon", "--show=NAME", "--noheadings", "--raw")
        ] = ("/dev/nvme0n1p3\n", "", 0)
        context = InstallContext(
            plan,
            lambda _message: None,
            values={
                "partition_devices": {
                    "swap": "/dev/nvme0n1p3",
                }
            },
        )

        PrepareStorageStep(runner).cleanup(context)

        commands = [item[0] for item in runner.commands]
        self.assertEqual(commands[1], ("swapoff", "/dev/nvme0n1p3"))
        self.assertIn(("partprobe", "/dev/nvme0n1"), commands)
        self.assertIn(("udevadm", "settle", "--timeout=30"), commands)

    def test_retries_partition_table_once_after_kernel_rejects_update(self):
        plan = valid_plan()
        runner = FakeRunner()
        first_partition_command = (
            "parted",
            "--script",
            "/dev/nvme0n1",
            "mklabel",
            "gpt",
        )
        calls = 0
        original_run = runner.run

        def run(command, **kwargs):
            nonlocal calls
            if tuple(command) == first_partition_command:
                calls += 1
                if calls == 1:
                    runner.commands.append((tuple(command), kwargs))
                    return subprocess.CompletedProcess(
                        command,
                        1,
                        "",
                        "unable to inform the kernel of the change",
                    )
            if tuple(command) == ("partprobe", "/dev/nvme0n1"):
                runner.commands.append((tuple(command), kwargs))
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "partition table is still in use",
                )
            return original_run(command, **kwargs)

        runner.run = run
        logs = []
        context = InstallContext(plan, logs.append)

        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            PrepareStorageStep(runner).execute(context)

        self.assertEqual(calls, 2)
        self.assertIn("retrying once", "\n".join(logs))


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

    def test_verifies_every_mount_comes_from_selected_disk(self):
        runner = FakeRunner()
        target = Path("/target-test")
        mounts = [
            target,
            target / "home",
            target / "var/log",
        ]
        expected = {
            target: "/dev/sdb4[/@root]\n",
            target / "boot/efi": "/dev/sdb2\n",
            target / "home": "/dev/sdb4[/@home]\n",
            target / "var/log": "/dev/sdb4[/@log]\n",
        }
        for path, source in expected.items():
            runner.outputs[
                (
                    "findmnt",
                    "--noheadings",
                    "--output",
                    "SOURCE",
                    "--mountpoint",
                    str(path),
                )
            ] = (source, "", 0)
        logs = []
        context = InstallContext(
            valid_plan(),
            logs.append,
            values={
                "partition_devices": {
                    "root": "/dev/sdb4",
                    "efi-system": "/dev/sdb2",
                },
                "target_btrfs_mounts": mounts,
            },
        )

        MountTargetStep(runner, target=target).verify(context)

        self.assertEqual(
            sum("Verified mount source" in item for item in logs),
            len(expected),
        )

    def test_rejects_efi_mounted_from_another_windows_disk(self):
        runner = FakeRunner()
        target = Path("/target-test")
        runner.outputs[
            (
                "findmnt",
                "--noheadings",
                "--output",
                "SOURCE",
                "--mountpoint",
                str(target),
            )
        ] = ("/dev/sdb4\n", "", 0)
        runner.outputs[
            (
                "findmnt",
                "--noheadings",
                "--output",
                "SOURCE",
                "--mountpoint",
                str(target / "boot/efi"),
            )
        ] = ("/dev/sda1\n", "", 0)
        context = InstallContext(
            valid_plan(),
            lambda _message: None,
            values={
                "partition_devices": {
                    "root": "/dev/sdb4",
                    "efi-system": "/dev/sdb2",
                },
                "target_btrfs_mounts": [target],
            },
        )

        with self.assertRaisesRegex(
            RuntimeError, "expected /dev/sdb2, found /dev/sda1"
        ):
            MountTargetStep(runner, target=target).verify(context)

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
