import os
import stat
import unittest
from dataclasses import replace
from unittest import mock

from fakes import FakeRunner
from helpers import (
    TEST_PHYSICAL_MEMORY_BYTES,
    valid_inventory,
    valid_plan,
)
from installer_core.preflight import (
    NamespaceMount,
    PreflightError,
    _find_target_mount,
    verify_execution_environment,
    verify_target_disk_environment,
)
from installer_core.probe import PlatformProbe


class ExecutionPreflightTests(unittest.TestCase):
    def setUp(self):
        namespace_probe = mock.patch(
            "installer_core.preflight.probe_cross_namespace_target_mount",
            return_value=None,
        )
        namespace_probe.start()
        self.addCleanup(namespace_probe.stop)
        memory_probe = mock.patch(
            "installer_core.preflight.probe_physical_memory_bytes",
            return_value=TEST_PHYSICAL_MEMORY_BYTES,
        )
        memory_probe.start()
        self.addCleanup(memory_probe.stop)

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

    @staticmethod
    def memory_probe():
        return TEST_PHYSICAL_MEMORY_BYTES

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
            inventory_probe=lambda: valid_inventory(plan),
        )
        self.assertTrue(runner.root_checked)
        self.assertEqual(
            runner.commands[-1][0][-1], plan.storage.disk.path
        )

    def test_rejects_disk_substitution_at_same_path(self):
        plan = valid_plan()
        replacement = replace(plan.storage.disk, stable_id="serial:attacker")
        replacement_plan = replace(
            plan,
            storage=replace(plan.storage, disk=replacement),
        )
        with self.assertRaisesRegex(PreflightError, "no longer present"):
            verify_execution_environment(
                plan,
                FakeRunner(),
                platform_probe=lambda: PlatformProbe(
                    plan.platform.architecture,
                    plan.platform.firmware,
                    plan.platform.secure_boot,
                ),
                inventory_probe=lambda: valid_inventory(replacement_plan),
            )

    def test_resolves_current_path_before_usage_checks(self):
        plan = valid_plan()
        current_path = "/dev/nvme8n1"
        runner = self.idle_target_runner(current_path)
        resolved = verify_target_disk_environment(
            plan,
            runner,
            inventory_probe=lambda: valid_inventory(plan, path=current_path),
        )
        self.assertEqual(resolved.storage.disk.path, current_path)
        self.assertEqual(runner.commands[-1][0][-1], current_path)

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
                inventory_probe=lambda: valid_inventory(plan),
            )

    def test_accepts_custom_swap_when_memory_changes_within_safe_range(self):
        plan = valid_plan()
        runner = self.idle_target_runner()
        verify_target_disk_environment(
            plan,
            runner,
            inventory_probe=lambda: valid_inventory(plan),
            physical_memory_probe=lambda: 16 * 1024**3,
        )

    def test_rejects_custom_swap_beyond_current_safe_memory_limit(self):
        plan = valid_plan(swap_size_mib=9 * 1024)
        runner = self.idle_target_runner()
        with self.assertRaisesRegex(PreflightError, "safe maximum"):
            verify_target_disk_environment(
                plan,
                runner,
                inventory_probe=lambda: valid_inventory(plan),
                physical_memory_probe=lambda: 2 * 1024**3,
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
                inventory_probe=lambda: valid_inventory(plan),
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
                inventory_probe=lambda: valid_inventory(plan),
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
            inventory_probe=lambda: valid_inventory(plan),
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
                inventory_probe=lambda: valid_inventory(plan),
            )

    def test_rejects_target_mount_hidden_in_another_namespace(self):
        plan = valid_plan()
        runner = self.idle_target_runner()

        with self.assertRaisesRegex(
            PreflightError,
            "Restart the Live environment",
        ):
            verify_target_disk_environment(
                plan,
                runner,
                inventory_probe=lambda: valid_inventory(plan),
                namespace_mount_probe=lambda _paths: NamespaceMount(
                    "/dev/nvme0n1p4", "/target", 1071
                ),
            )

    def test_mountinfo_matches_btrfs_source_when_device_number_is_virtual(self):
        mountinfo = (
            "519 78 0:75 /@root /target rw,noatime shared:427 - "
            "btrfs /dev/vda4 rw,compress=zstd:3\n"
        )

        with mock.patch("installer_core.preflight.os.stat") as stat_call:
            source_stat = stat_call.return_value
            source_stat.st_mode = stat.S_IFBLK
            source_stat.st_rdev = os.makedev(253, 4)
            match = _find_target_mount(
                mountinfo,
                {(253, 4): "/dev/vda4"},
                1071,
            )

        self.assertEqual(
            match,
            NamespaceMount("/dev/vda4", "/target", 1071),
        )
