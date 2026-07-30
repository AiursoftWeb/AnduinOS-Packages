import unittest
from dataclasses import replace

from fakes import FakeRunner
from helpers import valid_plan
from installer_core.preflight import (
    PreflightError,
    verify_execution_environment,
    verify_target_disk_environment,
)
from installer_core.probe import PlatformProbe


class ExecutionPreflightTests(unittest.TestCase):
    def idle_target_runner(self, disk="/dev/nvme0n1"):
        runner = FakeRunner()
        runner.outputs[
            (
                "lsblk",
                "--json",
                "--paths",
                "--output",
                "PATH,TYPE,MOUNTPOINTS",
                disk,
            )
        ] = (
            '{"blockdevices":[{"path":"'
            + disk
            + '","type":"disk","mountpoints":[null],'
            '"children":[{"path":"'
            + disk
            + 'p1","type":"part","mountpoints":[null]}]}]}',
            "",
            0,
        )
        return runner

    def test_accepts_matching_platform_and_disk(self):
        plan = valid_plan()
        runner = self.idle_target_runner()
        verify_execution_environment(
            plan,
            runner,
            platform_probe=lambda: PlatformProbe(
                plan.platform.architecture,
                plan.platform.firmware,
                plan.platform.secure_boot,
            ),
            disk_probe=lambda: (plan.storage.disk,),
        )
        self.assertTrue(runner.root_checked)
        self.assertEqual(
            runner.commands[-1][0][-1], plan.storage.disk.path
        )

    def test_rejects_disk_substitution_at_same_path(self):
        plan = valid_plan()
        replacement = replace(plan.storage.disk, stable_id="serial:attacker")
        with self.assertRaisesRegex(PreflightError, "identity changed"):
            verify_execution_environment(
                plan,
                FakeRunner(),
                platform_probe=lambda: PlatformProbe(
                    plan.platform.architecture,
                    plan.platform.firmware,
                    plan.platform.secure_boot,
                ),
                disk_probe=lambda: (replacement,),
            )

    def test_rejects_secure_boot_state_change(self):
        plan = valid_plan()
        changed = replace(
            plan.platform,
            secure_boot=plan.platform.secure_boot.DISABLED,
        )
        with self.assertRaisesRegex(PreflightError, "Platform changed"):
            verify_execution_environment(
                plan,
                FakeRunner(),
                platform_probe=lambda: PlatformProbe(
                    changed.architecture,
                    changed.firmware,
                    changed.secure_boot,
                ),
                disk_probe=lambda: (plan.storage.disk,),
            )

    def test_rejects_mounted_partition_on_selected_disk(self):
        plan = valid_plan()
        runner = self.idle_target_runner()
        command = runner.commands
        self.assertEqual(command, [])
        key = next(iter(runner.outputs))
        runner.outputs[key] = (
            '{"blockdevices":[{"path":"/dev/nvme0n1","type":"disk",'
            '"mountpoints":[null],"children":[{"path":"/dev/nvme0n1p1",'
            '"type":"part","mountpoints":["/media/data"]}]}]}',
            "",
            0,
        )
        with self.assertRaisesRegex(PreflightError, "mounted at /media/data"):
            verify_target_disk_environment(
                plan,
                runner,
                disk_probe=lambda: (plan.storage.disk,),
            )

    def test_rejects_active_device_mapper_descendant(self):
        plan = valid_plan()
        runner = self.idle_target_runner()
        key = next(iter(runner.outputs))
        runner.outputs[key] = (
            '{"blockdevices":[{"path":"/dev/nvme0n1","type":"disk",'
            '"mountpoints":[null],"children":[{"path":"/dev/dm-0",'
            '"type":"crypt","mountpoints":[null]}]}]}',
            "",
            0,
        )
        with self.assertRaisesRegex(PreflightError, "in use by crypt"):
            verify_target_disk_environment(
                plan,
                runner,
                disk_probe=lambda: (plan.storage.disk,),
            )

    def test_allows_expected_swap_partition_from_previous_attempt(self):
        plan = valid_plan()
        runner = self.idle_target_runner()
        key = next(iter(runner.outputs))
        runner.outputs[key] = (
            '{"blockdevices":[{"path":"/dev/nvme0n1","type":"disk",'
            '"mountpoints":[null],"children":[{"path":"/dev/nvme0n1p3",'
            '"type":"part","mountpoints":["[SWAP]"]}]}]}',
            "",
            0,
        )

        verify_target_disk_environment(
            plan,
            runner,
            disk_probe=lambda: (plan.storage.disk,),
        )

    def test_rejects_unexpected_swap_partition_on_selected_disk(self):
        plan = valid_plan()
        runner = self.idle_target_runner()
        key = next(iter(runner.outputs))
        runner.outputs[key] = (
            '{"blockdevices":[{"path":"/dev/nvme0n1","type":"disk",'
            '"mountpoints":[null],"children":[{"path":"/dev/nvme0n1p2",'
            '"type":"part","mountpoints":["[SWAP]"]}]}]}',
            "",
            0,
        )

        with self.assertRaisesRegex(PreflightError, "mounted at \\[SWAP\\]"):
            verify_target_disk_environment(
                plan,
                runner,
                disk_probe=lambda: (plan.storage.disk,),
            )
