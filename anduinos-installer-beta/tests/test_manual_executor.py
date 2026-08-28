import unittest
from dataclasses import replace
from unittest.mock import patch

from fakes import FakeRunner
from test_guided_storage_planning import healthy_esp
from test_manual_layout import selection
from test_manual_layout import manual_disk
from test_manual_storage_graph import manual_plan
from installer_core.esp import NvramInspection
from installer_core.manual_layout import (
    ManualPartitionRequest,
    ManualPartitionResizeRequest,
    ManualPartitionRole,
)
from installer_core.model import Filesystem
from installer_core.preflight import (
    PreflightError,
    verify_target_disk_environment,
)
from installer_core.storage_inventory import (
    EFI_SYSTEM_PARTITION_GUID,
    PartitionIdentity,
    PartitionInventory,
    StorageInventory,
)
from installer_core.storage_graph import StorageGraphAction
from installer_core.ntfs_resize import NTFS_PROBE, NTFS_RESIZE
from installer_core.manual_write_set import build_manual_storage_write_set
from installer_core.steps import InstallContext
from installer_core.storage_preservation import (
    PreservationError,
    capture_manual_preservation_snapshot,
    verify_manual_storage_result,
)
from installer_core.storage_steps import PrepareStorageStep


def post_write_inventory(plan, inventory):
    disk = inventory.disks[0]
    graph = plan.storage.graph
    references = {
        item.reference_id: item.stable_id
        for item in graph.block_references
    }
    deleted_partuuids = {
        references[item.target_id]
        for item in graph.operations
        if item.action is StorageGraphAction.DELETE_PARTITION
    }
    replaces = any(
        item.action is StorageGraphAction.REPLACE_PARTITION_TABLE
        for item in graph.operations
    )
    resize_by_partuuid = {
        references[item.target_reference_id]: item
        for item in graph.partition_resizes
    }
    existing = () if replaces else tuple(
        replace(
            item,
            identity=replace(
                item.identity,
                size_bytes=resize_by_partuuid[
                    item.identity.partuuid
                ].target_size_bytes,
            ),
        )
        if item.identity.partuuid in resize_by_partuuid
        else item
        for item in disk.partitions
        if item.identity.partuuid not in deleted_partuuids
    )
    filesystems = {
        item.block_id: item.filesystem.value for item in graph.filesystems
    }
    additions = tuple(
        PartitionInventory(
            identity=PartitionIdentity(
                path=f"{disk.identity.path}p{item.number}",
                number=item.number,
                partuuid=f"new-part-{item.number}",
                start_bytes=item.start_mib * 1024 * 1024,
                size_bytes=(item.end_mib - item.start_mib) * 1024 * 1024,
            ),
            parent_disk_id=disk.identity.stable_id,
            partition_type=(
                EFI_SYSTEM_PARTITION_GUID
                if item.name == "efi-system"
                else "linux-test"
            ),
            filesystem_type=filesystems[item.partition_id],
            filesystem_uuid=f"new-fs-{item.number}",
            flags=item.flags,
        )
        for item in graph.partitions
    )
    updated = replace(
        disk,
        partition_table="gpt",
        partition_table_uuid=("new-gpt" if replaces else disk.partition_table_uuid),
        partitions=(*existing, *additions),
        free_extents=(),
        topology_digest="f" * 64,
    )
    return StorageInventory((updated,), "9" * 64)


def resized_only_inventory(plan, inventory):
    disk = inventory.disks[0]
    graph = plan.storage.graph
    references = {
        item.reference_id: item.stable_id for item in graph.block_references
    }
    targets = {
        references[item.target_reference_id]: item.target_size_bytes
        for item in graph.partition_resizes
    }
    partitions = tuple(
        replace(
            item,
            identity=replace(
                item.identity,
                size_bytes=targets[item.identity.partuuid],
            ),
        )
        if item.identity.partuuid in targets
        else item
        for item in disk.partitions
    )
    return StorageInventory(
        (
            replace(
                disk,
                partitions=partitions,
                topology_digest="8" * 64,
            ),
        ),
        "8" * 64,
    )


class ManualExecutorTests(unittest.TestCase):
    def test_preflight_requires_the_selected_advanced_formatter(self):
        for filesystem, formatter in (
            (Filesystem.XFS, "mkfs.xfs"),
            (Filesystem.F2FS, "mkfs.f2fs"),
        ):
            with self.subTest(filesystem=filesystem.value):
                plan, inventory = manual_plan(filesystem=filesystem)
                runner = FakeRunner()
                step = PrepareStorageStep(
                    runner,
                    inventory_probe=lambda: inventory,
                    esp_inspector=lambda esp, _runner: healthy_esp(
                        plan, inventory
                    ),
                    nvram_inspector=lambda _runner: NvramInspection(True),
                )
                step.preflight(InstallContext(plan, lambda _message: None))
                self.assertIn(formatter, runner.required)

    def test_ntfs_is_checked_twice_then_shrunk_before_partition_tail(self):
        disk = manual_disk()
        disk = replace(
            disk,
            partitions=(
                disk.partitions[0],
                replace(disk.partitions[1], filesystem_type="ntfs"),
            ),
        )
        resize = ManualPartitionResizeRequest(
            "part-2",
            disk.partitions[1].identity.size_bytes,
            40 * 1024,
        )
        chosen = selection(
            resized=(resize,),
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025 + 40 * 1024,
                    70 * 1024,
                ),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen, disk=disk)
        after_resize = resized_only_inventory(plan, inventory)
        after = post_write_inventory(plan, inventory)
        inventories = iter((inventory, after_resize, after))
        runner = FakeRunner()
        device = "/dev/nvme0n1p2"
        target = 40 * 1024**3
        runner.outputs[(NTFS_PROBE, "--readwrite", device)] = ("", "", 0)
        runner.outputs[
            (NTFS_RESIZE, "--check", "--no-action", device)
        ] = ("", "", 0)
        runner.outputs[
            (NTFS_RESIZE, "--info", "--no-action", device)
        ] = ("You might resize at 21474836480 bytes.\n", "", 0)
        runner.outputs[
            (
                NTFS_RESIZE,
                "--no-action",
                "--verbose",
                "--size",
                str(target),
                device,
            )
        ] = ("Every sanity check passed.\n", "", 0)
        step = PrepareStorageStep(
            runner,
            inventory_probe=lambda: next(inventories),
            esp_inspector=lambda esp, _runner: healthy_esp(plan, inventory),
            nvram_inspector=lambda _runner: NvramInspection(True),
        )
        context = InstallContext(plan, lambda _message: None)
        step.preflight(context)
        execution = context.values["manual_storage_execution_plan"]
        runner.outputs[
            (
                "blkid",
                "-s",
                "TYPE",
                "-o",
                "value",
                execution.commands.devices["efi-system"],
            )
        ] = ("vfat\n", "", 0)
        runner.outputs[
            (
                "blkid",
                "-s",
                "TYPE",
                "-o",
                "value",
                execution.commands.devices["root"],
            )
        ] = ("btrfs\n", "", 0)
        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            step.execute(context)
        step.verify(context)

        commands = [item[0] for item in runner.commands]
        actual_resize = (NTFS_RESIZE, "--size", str(target), device)
        boundary_resize = (
            "parted",
            "--script",
            "/dev/nvme0n1",
            "unit",
            "B",
            "resizepart",
            "2",
            f"{disk.partitions[1].identity.start_bytes + target - 1}B",
        )
        first_create = next(item for item in commands if "mkpart" in item)
        self.assertLess(commands.index(actual_resize), commands.index(boundary_resize))
        self.assertLess(commands.index(boundary_resize), commands.index(first_create))
        self.assertTrue(
            all(
                "--force" not in item and "-f" not in item
                for item in commands
                if item[0] == NTFS_RESIZE
            )
        )
        inspection_calls = [
            kwargs
            for command, kwargs in runner.commands
            if (
                command[0] == NTFS_PROBE
                or (
                    command[0] == NTFS_RESIZE
                    and "--no-action" in command
                )
            )
        ]
        self.assertTrue(inspection_calls)
        self.assertTrue(
            all(
                item["environment"]["LC_ALL"] == "C"
                and item["environment"]["LANGUAGE"] == "C"
                for item in inspection_calls
            )
        )

    def test_preflight_freezes_executes_and_verifies_manual_plan(self):
        plan, inventory = manual_plan()
        after = post_write_inventory(plan, inventory)
        inventories = iter((inventory, after))
        runner = FakeRunner()
        step = PrepareStorageStep(
            runner,
            inventory_probe=lambda: next(inventories),
            esp_inspector=lambda esp, _runner: healthy_esp(plan, inventory),
            nvram_inspector=lambda _runner: NvramInspection(True),
        )
        context = InstallContext(plan, lambda _message: None)
        step.preflight(context)
        execution = context.values["manual_storage_execution_plan"]
        self.assertIs(context.values["storage_execution_plan"], execution)
        for name, device in execution.commands.devices.items():
            filesystem = {
                "efi-system": "vfat",
                "root": "btrfs",
                "swap": "swap",
            }[name]
            runner.outputs[
                ("blkid", "-s", "TYPE", "-o", "value", device)
            ] = (filesystem + "\n", "", 0)
        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            step.execute(context)
        step.verify(context)
        flattened = {
            argument
            for command, _kwargs in runner.commands
            for argument in command
        }
        self.assertNotIn("resizepart", flattened)
        self.assertNotIn("move", flattened)
        self.assertNotIn(
            execution.commands.devices["efi-system"],
            {item[-1] for item in execution.commands.format},
        )

    def test_executor_refreshes_the_current_stable_disk_path(self):
        plan, inventory = manual_plan()
        disk = inventory.disks[0]
        current_disk = replace(
            disk,
            identity=replace(disk.identity, path="/dev/nvme7n1"),
            partitions=tuple(
                replace(
                    item,
                    identity=replace(
                        item.identity,
                        path=item.identity.path.replace(
                            "nvme0n1", "nvme7n1"
                        ),
                    ),
                )
                for item in disk.partitions
            ),
        )
        current_inventory = replace(inventory, disks=(current_disk,))
        runner = FakeRunner()
        step = PrepareStorageStep(
            runner,
            inventory_probe=lambda: current_inventory,
            esp_inspector=lambda esp, _runner: healthy_esp(
                plan, current_inventory
            ),
            nvram_inspector=lambda _runner: NvramInspection(True),
        )
        context = InstallContext(plan, lambda _message: None)

        step.preflight(context)
        with patch("installer_core.storage_steps.Path.exists", return_value=True):
            step.execute(context)

        self.assertEqual(context.values["storage_disk_path"], "/dev/nvme7n1")
        partprobes = [
            command
            for command, _kwargs in runner.commands
            if command[0] == "partprobe"
        ]
        self.assertTrue(partprobes)
        self.assertTrue(
            all(command == ("partprobe", "/dev/nvme7n1") for command in partprobes)
        )

    def test_preserved_partition_drift_and_undeclared_results_are_fatal(self):
        plan, inventory = manual_plan()
        snapshot = capture_manual_preservation_snapshot(
            plan,
            inventory,
            build_manual_storage_write_set(plan, inventory),
        )
        after = post_write_inventory(plan, inventory)
        changed = replace(
            after.disks[0].partitions[1],
            filesystem_uuid="changed",
        )
        drifted = replace(
            after,
            disks=(
                replace(
                    after.disks[0],
                    partitions=(
                        after.disks[0].partitions[0],
                        changed,
                        *after.disks[0].partitions[2:],
                    ),
                ),
            ),
        )
        with self.assertRaisesRegex(PreservationError, "Preserved partition changed"):
            verify_manual_storage_result(plan, snapshot, drifted)

    def test_reinitialized_gpt_may_change_table_identity_only(self):
        chosen = selection(
            reinitialize=True,
            reused_esp="",
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.EFI_SYSTEM,
                    1,
                    1025,
                ),
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025,
                    60_000,
                ),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen)
        snapshot = capture_manual_preservation_snapshot(
            plan,
            inventory,
            build_manual_storage_write_set(plan, inventory),
        )
        self.assertTrue(snapshot.reinitializes_gpt)
        self.assertFalse(snapshot.partitions)
        verify_manual_storage_result(
            plan,
            snapshot,
            post_write_inventory(plan, inventory),
        )

    def test_execute_cannot_skip_manual_all_step_preflight(self):
        plan, _inventory = manual_plan()
        runner = FakeRunner()
        with self.assertRaisesRegex(RuntimeError, "all-step preflight"):
            PrepareStorageStep(runner).execute(
                InstallContext(plan, lambda _message: None)
            )
        self.assertFalse(runner.commands)

    def test_only_explicitly_deleted_active_swap_passes_preflight(self):
        chosen = selection(
            deleted=("part-2",),
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025,
                    70_000,
                ),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen)
        disk = inventory.disks[0]
        active_swap = replace(
            disk.partitions[1],
            filesystem_type="swap",
            mountpoints=("[SWAP]",),
        )
        inventory = replace(
            inventory,
            disks=(
                replace(
                    disk,
                    partitions=(disk.partitions[0], active_swap),
                ),
            ),
        )
        runner = FakeRunner()
        lsblk = (
            "lsblk",
            "--json",
            "--paths",
            "--output",
            "PATH,TYPE,MOUNTPOINTS,PARTUUID",
            "/dev/nvme0n1",
        )
        runner.outputs[lsblk] = (
            '{"blockdevices":[{"path":"/dev/nvme0n1","type":"disk",'
            '"mountpoints":[null],"children":[{"path":"/dev/nvme0n1p2",'
            '"type":"part","partuuid":"part-2",'
            '"mountpoints":["[SWAP]"]}]}]}',
            "",
            0,
        )
        verify_target_disk_environment(
            plan,
            runner,
            inventory_probe=lambda: inventory,
            namespace_mount_probe=lambda _paths: None,
        )

        not_deleted_plan, not_deleted_inventory = manual_plan()
        with self.assertRaisesRegex(PreflightError, "mounted at \\[SWAP\\]"):
            verify_target_disk_environment(
                not_deleted_plan,
                runner,
                inventory_probe=lambda: not_deleted_inventory,
                namespace_mount_probe=lambda _paths: None,
            )


if __name__ == "__main__":
    unittest.main()
