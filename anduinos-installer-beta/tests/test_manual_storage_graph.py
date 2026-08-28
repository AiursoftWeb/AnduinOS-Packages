import json
import unittest
from dataclasses import replace

from helpers import TEST_INVENTORY_DIGEST, valid_plan
from test_manual_layout import manual_disk, selection
from installer_core.manual_graph_planning import (
    ManualStorageGraphError,
    build_manual_storage_graph,
    validate_manual_storage_graph,
)
from installer_core.manual_layout import (
    ManualPartitionRequest,
    ManualPartitionResizeRequest,
    ManualPartitionRole,
)
from installer_core.model import Filesystem, InstallMode, InstallPlan
from installer_core.storage_graph import (
    BlockReferenceKind,
    StorageGraphAction,
    StorageGraphMode,
)
from installer_core.storage_graph_planning import validate_storage_graph
from installer_core.storage_inventory import StorageInventory
from installer_core.validation import validate_plan


def manual_plan(
    *,
    chosen=None,
    filesystem=Filesystem.BTRFS,
    disk=None,
):
    disk = disk or manual_disk()
    chosen = chosen or selection(filesystem=filesystem)
    swap_size = next(
        (
            item.size_mib
            for item in chosen.new_partitions
            if item.role is ManualPartitionRole.SWAP
        ),
        0,
    )
    base = valid_plan(filesystem=filesystem, swap_size_mib=0)
    draft = replace(
        base,
        storage=replace(
            base.storage,
            mode=InstallMode.MANUAL,
            disk=disk.identity,
            filesystem=filesystem,
            swap_size_mib=swap_size,
            graph=None,
        ),
        boot=replace(base.boot, install_fallback_path=False),
    )
    graph = build_manual_storage_graph(
        draft,
        disk,
        chosen,
        inventory_digest=TEST_INVENTORY_DIGEST,
    )
    plan = replace(draft, storage=replace(draft.storage, graph=graph))
    inventory = StorageInventory((disk,), TEST_INVENTORY_DIGEST)
    return plan, inventory


class ManualStorageGraphTests(unittest.TestCase):
    def test_xfs_and_f2fs_are_canonical_single_root_graphs(self):
        for filesystem in (Filesystem.XFS, Filesystem.F2FS):
            with self.subTest(filesystem=filesystem.value):
                plan, inventory = manual_plan(filesystem=filesystem)
                validate_plan(plan)
                validate_manual_storage_graph(plan, inventory)
                graph = plan.storage.graph
                root = next(
                    item
                    for item in graph.filesystems
                    if item.filesystem.value == filesystem.value
                )
                self.assertEqual(root.label, "AnduinOS")
                self.assertFalse(graph.subvolumes)
                self.assertEqual(
                    tuple(
                        item.target_path
                        for item in graph.mounts
                        if item.target_path == "/"
                    ),
                    ("/",),
                )
                self.assertEqual(
                    InstallPlan.from_dict(plan.to_dict()),
                    plan,
                )

    def test_ntfs_resize_is_a_typed_existing_partition_operation(self):
        disk = manual_disk()
        disk = replace(
            disk,
            partitions=(
                disk.partitions[0],
                replace(disk.partitions[1], filesystem_type="ntfs"),
            ),
        )
        request = ManualPartitionResizeRequest(
            "part-2",
            disk.partitions[1].identity.size_bytes,
            40 * 1024,
        )
        chosen = selection(
            resized=(request,),
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025 + 40 * 1024,
                    70 * 1024,
                ),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen, disk=disk)
        validate_manual_storage_graph(plan, inventory)
        graph = plan.storage.graph
        self.assertEqual(len(graph.partition_resizes), 1)
        declaration = graph.partition_resizes[0]
        self.assertEqual(declaration.target_size_bytes, 40 * 1024**3)
        self.assertEqual(
            tuple(
                item.action
                for item in graph.operations
                if item.action is StorageGraphAction.RESIZE_PARTITION
            ),
            (StorageGraphAction.RESIZE_PARTITION,),
        )
        self.assertNotIn(
            declaration.target_reference_id,
            {
                item.target_id
                for item in graph.operations
                if item.action is StorageGraphAction.PRESERVE
            },
        )

    def test_keep_table_preserves_existing_esp_and_data(self):
        plan, inventory = manual_plan()
        graph = plan.storage.graph
        validate_plan(plan)
        self.assertIs(graph.mode, StorageGraphMode.MANUAL)
        self.assertIs(
            validate_manual_storage_graph(plan, inventory),
            inventory.disks[0],
        )
        preserved = tuple(
            item.target_id
            for item in graph.operations
            if item.action is StorageGraphAction.PRESERVE
        )
        self.assertEqual(len(preserved), 2)
        self.assertFalse(
            any(
                item.action is StorageGraphAction.DELETE_PARTITION
                for item in graph.operations
            )
        )
        esp_id = graph.boot_targets[0].efi_filesystem_id
        formatted = {
            item.target_id
            for item in graph.operations
            if item.action is StorageGraphAction.FORMAT
        }
        self.assertNotIn(esp_id, formatted)

    def test_deleted_partition_is_explicit_and_remaining_partition_is_preserved(self):
        chosen = selection(
            deleted=("part-2",),
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    1025,
                    40 * 1024,
                ),
                ManualPartitionRequest(
                    ManualPartitionRole.SWAP,
                    40 * 1024,
                    40 * 1024 + 1537,
                ),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen)
        validate_manual_storage_graph(plan, inventory)
        graph = plan.storage.graph
        deleted = tuple(
            item.target_id
            for item in graph.operations
            if item.action is StorageGraphAction.DELETE_PARTITION
        )
        self.assertEqual(len(deleted), 1)
        reference = next(
            item
            for item in graph.block_references
            if item.reference_id == deleted[0]
        )
        self.assertEqual(reference.stable_id, "part-2")
        self.assertEqual(plan.storage.swap_size_mib, 1537)

    def test_reinitialize_gpt_has_no_existing_partition_references(self):
        chosen = selection(
            reinitialize=True,
            reused_esp="",
            new_partitions=(
                ManualPartitionRequest(ManualPartitionRole.EFI_SYSTEM, 1, 1025),
                ManualPartitionRequest(ManualPartitionRole.ROOT, 1025, 60 * 1024),
            ),
        )
        plan, inventory = manual_plan(chosen=chosen)
        validate_manual_storage_graph(plan, inventory)
        graph = plan.storage.graph
        self.assertEqual(
            tuple(item.kind for item in graph.block_references),
            (BlockReferenceKind.DISK,),
        )
        self.assertEqual(
            graph.operations[0].action,
            StorageGraphAction.REPLACE_PARTITION_TABLE,
        )

    def test_graph_round_trip_has_no_commands_or_device_paths(self):
        plan, _inventory = manual_plan()
        restored = InstallPlan.from_dict(plan.to_dict())
        self.assertEqual(restored, plan)
        encoded = json.dumps(plan.storage.graph.to_dict())
        self.assertNotIn("/dev/", encoded)
        self.assertNotIn("parted", encoded)
        self.assertNotIn("mkfs", encoded)

    def test_existing_partition_format_injection_fails_closed(self):
        plan, _inventory = manual_plan()
        graph = plan.storage.graph
        esp_id = graph.boot_targets[0].efi_filesystem_id
        changed = replace(
            graph,
            operations=(
                *graph.operations,
                replace(
                    graph.operations[-1],
                    action=StorageGraphAction.FORMAT,
                    target_id=esp_id,
                ),
            ),
        )
        changed_plan = replace(
            plan,
            storage=replace(plan.storage, graph=changed),
        )
        with self.assertRaisesRegex(
            ManualStorageGraphError,
            "no existing partition",
        ):
            validate_storage_graph(changed_plan)

    def test_changed_topology_is_rejected_before_command_compilation(self):
        plan, inventory = manual_plan()
        changed = replace(
            inventory.disks[0],
            topology_digest="f" * 64,
        )
        with self.assertRaisesRegex(
            ManualStorageGraphError,
            "topology changed",
        ):
            validate_manual_storage_graph(
                plan,
                replace(inventory, disks=(changed,)),
            )


if __name__ == "__main__":
    unittest.main()
