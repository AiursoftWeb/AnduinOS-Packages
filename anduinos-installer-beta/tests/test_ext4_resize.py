import contextlib
import io
import json
import subprocess
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from fakes import FakeRunner
from frontend import FrontendPlanError, probe_partition_resize
from installer_core.ext4_resize import (
    DUMPE2FS, E2FSCK, RESIZE2FS, inspect_ext4_resize,
    parse_ext4_geometry, shrink_ext4_filesystem,
)
from installer_core.ntfs_resize import GIB, MIB
from installer_core.resize import ResizeBlockReason, ResizeInspection
from installer_core.storage_graph import GraphFilesystem
from installer_core.manual_layout import (
    ManualLayoutError, ManualPartitionRequest, ManualPartitionResizeRequest,
    ManualPartitionRole, validate_manual_selection,
)
from installer_core.esp import NvramInspection
from installer_core.model import InstallPlan
from installer_core.steps import InstallContext
from installer_core.storage_planning import build_manual_storage_execution_plan
from installer_core.storage_steps import PrepareStorageStep
from installer_core.storage_preservation import (
    PreservationError, capture_manual_preservation_snapshot, verify_manual_storage_result,
)
from storage_probe_cli import main
from test_manual_layout import manual_disk, selection
from test_manual_storage_graph import manual_plan
from test_manual_executor import post_write_inventory, resized_only_inventory
from test_guided_storage_planning import healthy_esp


UUID = "12345678-1234-1234-1234-123456789abc"


def header(size=64 * GIB, *, block_size=4096, features=None, state="clean", uuid=UUID):
    return (f"Filesystem UUID: {uuid}\nFilesystem magic number: 0xEF53\n"
            f"Filesystem features: {features or 'has_journal extent 64bit metadata_csum orphan_file'}\n"
            f"Filesystem state: {state}\nBlock count: {size // block_size}\n"
            f"Free blocks: {(size - 8 * GIB) // block_size}\nBlock size: {block_size}\n")


def readonly_run(command, **options):
    if command[0] == DUMPE2FS:
        output = header()
    elif command[0] == E2FSCK:
        assert "-n" in command
        output = ""
    else:
        assert command[:2] == [RESIZE2FS, "-P"]
        output = "Estimated minimum size of the filesystem: 2097152\n"
    assert options["env"]["LC_ALL"] == "C"
    return subprocess.CompletedProcess(command, 0, output, "")


def ext4_plan():
    disk = manual_disk()
    part = replace(disk.partitions[1], filesystem_uuid=UUID)
    disk = replace(disk, partitions=(disk.partitions[0], part))
    chosen = selection(resized=(ManualPartitionResizeRequest(
        part.identity.partuuid, part.identity.size_bytes, 40 * 1024),),
        new_partitions=(ManualPartitionRequest(
            ManualPartitionRole.ROOT, 1025 + 40 * 1024, 70 * 1024),))
    return manual_plan(chosen=chosen, disk=disk)


class Ext4InspectionTests(unittest.TestCase):
    def test_healthy_inspection_is_read_only_conservative_and_serializable(self):
        inspection = inspect_ext4_resize("/dev/sda2", 64 * GIB,
            target_size_bytes=32 * GIB, run=readonly_run)
        self.assertTrue(inspection.safe)
        self.assertEqual(inspection.minimum_size_bytes, 10 * GIB)
        self.assertEqual(inspection.maximum_size_bytes, 64 * GIB - MIB)
        self.assertEqual(ResizeInspection.from_json(inspection.to_json()), inspection)

    def test_mounted_wrong_filesystem_and_missing_size_never_run_tools(self):
        for options, reason in (({"mounted": True}, ResizeBlockReason.MOUNTED),
                ({"filesystem": "btrfs"}, ResizeBlockReason.NOT_EXT4),
                ({"current_size_bytes": 0}, ResizeBlockReason.RANGE_UNAVAILABLE)):
            with self.subTest(options=options):
                run = unittest.mock.Mock(side_effect=AssertionError("No tools"))
                result = inspect_ext4_resize("/dev/sda2", run=run,
                    **{"current_size_bytes": 64 * GIB, **options})
                self.assertIs(result.block_reason, reason)
                run.assert_not_called()

    def test_dirty_special_features_small_blocks_and_bad_geometry_are_rejected(self):
        for output in (header(state="not clean"), header(block_size=1024),
                header(block_size=2048), header(features="has_journal extent bigalloc"),
                header(features="has_journal extent encrypt"),
                header() + "Journal device: 0x1234\n",
                header().replace("0xEF53", "0xAAAA"),
                header().replace("Block count:", "Missing:"),
                header() + "Block count: 1234\n"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                parse_ext4_geometry(output)

    def test_read_only_failures_never_fall_back_to_repair_or_force(self):
        for stage in (DUMPE2FS, E2FSCK, RESIZE2FS):
            for code in (1, 2, 4, 8, 127):
                calls = []
                def run(command, **options):
                    calls.append(command)
                    if command[0] == stage:
                        return subprocess.CompletedProcess(command, code, "", "error")
                    return readonly_run(command, **options)
                with self.subTest(stage=stage, code=code):
                    result = inspect_ext4_resize("/dev/sda2", 64 * GIB, run=run)
                    self.assertFalse(result.safe)
                    self.assertTrue(all("-y" not in cmd and "-p" not in cmd for cmd in calls))

    def test_minimum_is_not_trusted_when_missing_or_implausible(self):
        for output in ("", "Estimated minimum size of the filesystem: 0\n",
                "Estimated minimum size of the filesystem: 999999999999\n"):
            def run(command, **options):
                if command[0] == RESIZE2FS:
                    return subprocess.CompletedProcess(command, 0, output, "")
                return readonly_run(command, **options)
            self.assertFalse(inspect_ext4_resize("/dev/sda2", 64 * GIB, run=run).safe)

    def test_target_range_and_alignment_are_checked(self):
        for target in (8 * GIB, 64 * GIB, 32 * GIB + 1):
            self.assertIs(inspect_ext4_resize("/dev/sda2", 64 * GIB,
                target_size_bytes=target, run=readonly_run).block_reason,
                ResizeBlockReason.TARGET_OUT_OF_RANGE)

    def test_missing_tools_and_timeout_fail_closed(self):
        for error in (FileNotFoundError("missing"), subprocess.TimeoutExpired("tool", 10)):
            self.assertFalse(inspect_ext4_resize("/dev/sda2", 64 * GIB,
                run=unittest.mock.Mock(side_effect=error)).safe)

    def test_filesystem_must_fit_inside_the_current_partition(self):
        self.assertFalse(inspect_ext4_resize("/dev/sda2", 32 * GIB, run=readonly_run).safe)

    def test_helper_and_frontend_dispatch_are_read_only_and_bound_to_filesystem(self):
        def run(command, **options):
            if command[0] == "/usr/bin/lsblk":
                return subprocess.CompletedProcess(command, 0, json.dumps({"blockdevices": [{
                    "path": "/dev/sda2", "type": "part", "rm": False,
                    "fstype": "ext4", "partuuid": "test", "size": 64 * GIB,
                    "mountpoints": []}]}), "")
            return readonly_run(command, **options)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["--ext4-inspect", "/dev/sda2"],
                run=run, geteuid=lambda: 0), 0)
        assessment = ResizeInspection.from_json(output.getvalue())
        self.assertTrue(assessment.safe)
        with patch("frontend.os.geteuid", return_value=1000), \
                patch("frontend.subprocess.run", return_value=subprocess.CompletedProcess(
                    [], 0, assessment.to_json(), "")) as request:
            self.assertEqual(probe_partition_resize("/dev/sda2", filesystem="ext4"), assessment)
            self.assertEqual(request.call_args.args[0][2], "--ext4-inspect")
            with self.assertRaises(FrontendPlanError):
                probe_partition_resize("/dev/sda2", filesystem="ntfs")
        with contextlib.redirect_stderr(io.StringIO()):
            forbidden = unittest.mock.Mock(side_effect=AssertionError("No command"))
            self.assertEqual(main(["--ext4-inspect", "/tmp/image"],
                run=forbidden, geteuid=lambda: 0), 2)
            forbidden.assert_not_called()


class Ext4ExecutionTests(unittest.TestCase):
    def test_complete_executor_shrinks_checks_then_edits_gpt_before_creating_root(self):
        plan, inventory = ext4_plan()
        inventories = iter((inventory, inventory, resized_only_inventory(plan, inventory),
                            post_write_inventory(plan, inventory)))
        runner = FakeRunner()
        device = "/dev/nvme0n1p2"
        resized = False
        def run(command, **options):
            nonlocal resized
            command = tuple(command)
            result = FakeRunner.run(runner, command, **options)
            if command[0] == DUMPE2FS:
                return subprocess.CompletedProcess(command, 0,
                    header(40 * GIB if resized else 64 * GIB), "")
            if command[:2] == (RESIZE2FS, "-P"):
                return subprocess.CompletedProcess(command, 0,
                    "Estimated minimum size of the filesystem: 2097152\n", "")
            if command == (RESIZE2FS, device, "40960M"):
                resized = True
            return result
        runner.run = run
        step = PrepareStorageStep(runner, inventory_probe=lambda: next(inventories),
            esp_inspector=lambda esp, r: healthy_esp(plan, inventory),
            nvram_inspector=lambda r: NvramInspection(True))
        context = InstallContext(plan, lambda message: None)
        step.preflight(context)
        execution = context.values["manual_storage_execution_plan"]
        for role, fs in (("efi-system", "vfat"), ("root", "btrfs")):
            runner.outputs[("blkid", "-s", "TYPE", "-o", "value",
                            execution.commands.devices[role])] = (fs, "", 0)
        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            step.execute(context)
        step.verify(context)
        commands = [cmd for cmd, _ in runner.commands]
        shrink = commands.index((RESIZE2FS, device, "40960M"))
        tail = next(i for i, cmd in enumerate(commands) if "resizepart" in cmd)
        create = next(i for i, cmd in enumerate(commands) if "mkpart" in cmd)
        self.assertLess(shrink, tail)
        self.assertLess(tail, create)
        self.assertIn((E2FSCK, "-f", "-n", device), commands[shrink:tail])
        self.assertIn((DUMPE2FS, "-h", device), commands[shrink:tail])
        self.assertEqual(2, commands.count((RESIZE2FS, "-P", device)))
        self.assertFalse(any(cmd[0].endswith("ntfsresize") for cmd in commands))

    def test_ext4_graph_round_trip_and_commands_are_declarative(self):
        plan, inventory = ext4_plan()
        self.assertEqual(InstallPlan.from_dict(plan.to_dict()), plan)
        self.assertIs(plan.storage.graph.partition_resizes[0].filesystem, GraphFilesystem.EXT4)
        inspection = inspect_ext4_resize("/dev/nvme0n1p2",
            inventory.disks[0].partitions[1].identity.size_bytes,
            target_size_bytes=40 * GIB, run=readonly_run)
        execution = build_manual_storage_execution_plan(plan, inventory,
            esp_inspection=healthy_esp(plan, inventory), nvram_inspection=NvramInspection(True),
            resize_inspections=(inspection,))
        self.assertIs(execution.commands.partition_resizes[0].filesystem, GraphFilesystem.EXT4)
        self.assertFalse(any("resizepart" in cmd for cmd in execution.commands.partition))
        for assessments in ((), (replace(inspection, filesystem="ntfs"),),
                (replace(inspection, target_size_bytes=32 * GIB),)):
            with self.assertRaises(RuntimeError):
                build_manual_storage_execution_plan(plan, inventory,
                    esp_inspection=healthy_esp(plan, inventory), nvram_inspection=NvramInspection(True),
                    resize_inspections=assessments)

    def test_btrfs_and_mounted_ext4_are_refused_in_layout(self):
        disk = manual_disk()
        chosen = selection(resized=(ManualPartitionResizeRequest("part-2",
            disk.partitions[1].identity.size_bytes, 40 * 1024),))
        for partition in (replace(disk.partitions[1], filesystem_type="btrfs"),
                          replace(disk.partitions[1], mountpoints=("/mnt",))):
            with self.assertRaises(ManualLayoutError):
                validate_manual_selection(replace(disk,
                    partitions=(disk.partitions[0], partition)), chosen)

    def test_shrink_checks_and_verifies_geometry_and_uuid(self):
        device = "/dev/sda2"
        for check_code in (0, 1, 2, 4, 8):
            runner = FakeRunner()
            headers = iter((header(), header(32 * GIB)))
            def run(command, **options):
                result = FakeRunner.run(runner, command, **options)
                if command[0] == DUMPE2FS:
                    return subprocess.CompletedProcess(command, 0, next(headers), "")
                if "-p" in command:
                    return subprocess.CompletedProcess(command, check_code, "", "")
                return result
            runner.run = run
            with self.subTest(check_code=check_code):
                if check_code in (0, 1):
                    shrink_ext4_filesystem(device, 32 * GIB, runner)
                    commands = [cmd for cmd, _ in runner.commands]
                    self.assertLess(commands.index((E2FSCK, "-f", "-p", device)),
                                    commands.index((RESIZE2FS, device, "32768M")))
                    self.assertIn((E2FSCK, "-f", "-n", device), commands)
                    self.assertFalse(any("-f" in cmd for cmd in commands if cmd[0] == RESIZE2FS))
                else:
                    with self.assertRaises(RuntimeError):
                        shrink_ext4_filesystem(device, 32 * GIB, runner)
                    self.assertFalse(any(cmd[0] == RESIZE2FS for cmd, _ in runner.commands))

    def test_success_exit_is_not_enough_without_actual_size_uuid_and_clean_check(self):
        for after, code in ((header(), 0), (header(32 * GIB, uuid=UUID.replace("abc", "def")), 0),
                            (header(32 * GIB), 4)):
            headers = iter((header(), after))
            def run(command, **options):
                return subprocess.CompletedProcess(command,
                    code if "-n" in command else 0,
                    next(headers) if command[0] == DUMPE2FS else "", "")
            with self.assertRaises(RuntimeError):
                shrink_ext4_filesystem("/dev/sda2", 32 * GIB, SimpleNamespace(run=run))

    def test_uuid_mismatch_prevents_even_the_authorized_repair(self):
        runner = FakeRunner()
        runner.outputs[(DUMPE2FS, "-h", "/dev/sda2")] = (header(), "", 0)
        with self.assertRaisesRegex(RuntimeError, "UUID changed"):
            shrink_ext4_filesystem("/dev/sda2", 32 * GIB, runner,
                                   expected_uuid=UUID.replace("abc", "def"))
        self.assertEqual([(DUMPE2FS, "-h", "/dev/sda2")],
                         [cmd for cmd, _ in runner.commands])

    def test_executor_stops_before_partition_edits_on_filesystem_failure(self):
        plan, inventory = ext4_plan()
        runner = FakeRunner()
        device = "/dev/nvme0n1p2"
        runner.outputs[(DUMPE2FS, "-h", device)] = (header(), "", 0)
        runner.outputs[(RESIZE2FS, "-P", device)] = (
            "Estimated minimum size of the filesystem: 2097152\n", "", 0)
        step = PrepareStorageStep(runner, inventory_probe=lambda: inventory,
            esp_inspector=lambda esp, r: healthy_esp(plan, inventory),
            nvram_inspector=lambda r: NvramInspection(True))
        context = InstallContext(plan, lambda message: None)
        step.preflight(context)
        self.assertTrue({"e2fsck", "resize2fs", "dumpe2fs"} <= set(runner.required))
        runner.commands.clear()
        with patch("installer_core.storage_steps.shrink_ext4_filesystem",
                side_effect=RuntimeError("failed")), self.assertRaisesRegex(RuntimeError, "failed"):
            step._execute_partition_resizes(context, context.values["manual_storage_execution_plan"])
        self.assertFalse(any("resizepart" in cmd or "mkpart" in cmd for cmd, _ in runner.commands))

    def test_changed_or_mounted_target_is_rejected_before_writes(self):
        plan, inventory = ext4_plan()
        device = "/dev/nvme0n1p2"
        runner = FakeRunner()
        runner.outputs[(DUMPE2FS, "-h", device)] = (header(), "", 0)
        runner.outputs[(RESIZE2FS, "-P", device)] = (
            "Estimated minimum size of the filesystem: 2097152\n", "", 0)
        step = PrepareStorageStep(runner, inventory_probe=lambda: inventory,
            esp_inspector=lambda esp, r: healthy_esp(plan, inventory),
            nvram_inspector=lambda r: NvramInspection(True))
        context = InstallContext(plan, lambda message: None)
        step.preflight(context)
        disk = inventory.disks[0]
        for part in (replace(disk.partitions[1], mountpoints=("/mnt",)),
                     replace(disk.partitions[1], filesystem_uuid="changed"),
                     replace(disk.partitions[1], filesystem_type="btrfs")):
            step.inventory_probe = lambda: replace(inventory,
                disks=(replace(disk, partitions=(disk.partitions[0], part)),))
            runner.commands.clear()
            with self.assertRaisesRegex(RuntimeError, "changed or became active"):
                step._execute_partition_resizes(context, context.values["manual_storage_execution_plan"])
            self.assertFalse(runner.commands)

    def test_preservation_accepts_shrunk_ext4_but_rejects_uuid_or_type_changes(self):
        plan, inventory = ext4_plan()
        from installer_core.manual_write_set import build_manual_storage_write_set
        snapshot = capture_manual_preservation_snapshot(plan, inventory,
            build_manual_storage_write_set(plan, inventory))
        after = post_write_inventory(plan, inventory)
        verify_manual_storage_result(plan, snapshot, after)
        disk = after.disks[0]
        for changed in (replace(disk.partitions[1], filesystem_uuid="changed"),
                        replace(disk.partitions[1], filesystem_type="ntfs")):
            with self.assertRaises(PreservationError):
                verify_manual_storage_result(plan, snapshot, replace(after,
                    disks=(replace(disk, partitions=(disk.partitions[0], changed,
                                                     *disk.partitions[2:])),)))
