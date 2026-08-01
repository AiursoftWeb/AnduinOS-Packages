import json
import unittest
from dataclasses import replace

from helpers import valid_plan
from test_coexistence import windows_disk
from installer_core.model import Filesystem, InstallMode, InstallPlan
from installer_core.storage_graph import (
    BlockReferenceKind,
    StorageGraphAction,
)
from installer_core.storage_graph_planning import (
    StorageGraphValidationError,
    build_guided_coexistence_storage_graph,
    validate_guided_coexistence_graph,
    validate_storage_graph,
)
from installer_core.storage_inventory import StorageInventory
from installer_core.validation import validate_plan


INVENTORY_DIGEST = "c" * 64


def guided_plan(*, free_gib=24, filesystem=Filesystem.BTRFS, reuse_esp=True):
    disk = windows_disk(free_gib=free_gib)
    base = valid_plan(filesystem=filesystem)
    draft = replace(
        base,
        storage=replace(
            base.storage,
            mode=InstallMode.GUIDED_COEXISTENCE,
            disk=disk.identity,
            graph=None,
        ),
        boot=replace(base.boot, install_fallback_path=False),
    )
    extent = disk.free_extents[0]
    esp = disk.partitions[0] if reuse_esp else None
    graph = build_guided_coexistence_storage_graph(
        draft,
        disk,
        extent,
        inventory_digest=INVENTORY_DIGEST,
        reused_esp=esp,
    )
    plan = replace(draft, storage=replace(draft.storage, graph=graph))
    inventory = StorageInventory((disk,), INVENTORY_DIGEST)
    return plan, inventory


class GuidedStorageGraphTests(unittest.TestCase):
    def test_reused_esp_preserves_every_existing_partition(self):
        plan, inventory = guided_plan(free_gib=20.5)
        graph = plan.storage.graph
        validate_storage_graph(plan)
        self.assertIs(
            validate_guided_coexistence_graph(plan, inventory),
            inventory.disks[0],
        )
        partition_references = tuple(
            item
            for item in graph.block_references
            if item.kind is BlockReferenceKind.PARTITION
        )
        preserved = tuple(
            item.target_id
            for item in graph.operations
            if item.action is StorageGraphAction.PRESERVE
        )
        self.assertEqual(
            preserved,
            tuple(item.reference_id for item in partition_references),
        )
        esp_id = graph.boot_targets[0].efi_filesystem_id
        formatted = {
            item.target_id
            for item in graph.operations
            if item.action is StorageGraphAction.FORMAT
        }
        self.assertNotIn(esp_id, formatted)
        self.assertEqual(graph.boot_targets[0].fallback_path, "")
        self.assertFalse(graph.boot_targets[0].bios_disk_reference_id)

    def test_new_dedicated_esp_is_created_only_inside_selected_extent(self):
        plan, inventory = guided_plan(reuse_esp=False)
        graph = plan.storage.graph
        validate_guided_coexistence_graph(plan, inventory)
        self.assertEqual(
            tuple(item.name for item in graph.partitions),
            ("efi-system", "swap", "root"),
        )
        extent = next(
            item
            for item in graph.block_references
            if item.kind is BlockReferenceKind.FREE_EXTENT
        )
        self.assertTrue(
            all(
                item.parent_reference_id == extent.reference_id
                for item in graph.partitions
            )
        )
        esp_id = graph.boot_targets[0].efi_filesystem_id
        self.assertIn(
            (StorageGraphAction.FORMAT, esp_id),
            tuple((item.action, item.target_id) for item in graph.operations),
        )

    def test_graph_round_trip_contains_no_device_paths_or_commands(self):
        plan, _inventory = guided_plan()
        restored = InstallPlan.from_dict(plan.to_dict())
        self.assertEqual(restored, plan)
        encoded = json.dumps(plan.storage.graph.to_dict())
        self.assertNotIn("/dev/", encoded)
        self.assertNotIn("parted", encoded)
        self.assertNotIn("mkfs", encoded)

    def test_ext4_has_no_subvolume_declarations(self):
        plan, inventory = guided_plan(filesystem=Filesystem.EXT4)
        validate_guided_coexistence_graph(plan, inventory)
        self.assertEqual(plan.storage.graph.subvolumes, ())

    def test_changed_topology_or_extent_is_rejected(self):
        plan, inventory = guided_plan()
        disk = inventory.disks[0]
        stale_topology = replace(disk, topology_digest="d" * 64)
        with self.assertRaisesRegex(
            StorageGraphValidationError, "topology changed"
        ):
            validate_guided_coexistence_graph(
                plan,
                replace(inventory, disks=(stale_topology,)),
            )

        moved_extent = replace(
            disk.free_extents[0],
            start_bytes=disk.free_extents[0].start_bytes + 1024**2,
        )
        changed_disk = replace(disk, free_extents=(moved_extent,))
        with self.assertRaisesRegex(
            StorageGraphValidationError, "free extent changed"
        ):
            validate_guided_coexistence_graph(
                plan,
                replace(inventory, disks=(changed_disk,)),
            )

    def test_missing_preserve_declaration_is_rejected(self):
        plan, _inventory = guided_plan()
        graph = plan.storage.graph
        removed_one = False
        operations = []
        for item in graph.operations:
            if item.action is StorageGraphAction.PRESERVE and not removed_one:
                removed_one = True
                continue
            operations.append(item)
        changed = replace(graph, operations=tuple(operations))
        changed_plan = replace(
            plan,
            storage=replace(plan.storage, graph=changed),
        )
        with self.assertRaisesRegex(
            StorageGraphValidationError, "explicitly preserved"
        ):
            validate_storage_graph(changed_plan)

    def test_whole_disk_and_fallback_actions_are_rejected(self):
        plan, _inventory = guided_plan()
        graph = plan.storage.graph
        changed = replace(
            graph,
            operations=(
                *graph.operations,
                replace(
                    graph.operations[0],
                    action=StorageGraphAction.REPLACE_PARTITION_TABLE,
                ),
            ),
        )
        changed_plan = replace(
            plan,
            storage=replace(plan.storage, graph=changed),
        )
        with self.assertRaisesRegex(
            StorageGraphValidationError, "whole-disk or fallback write"
        ):
            validate_storage_graph(changed_plan)

    def test_beta_validation_accepts_guided_write_graph(self):
        plan, _inventory = guided_plan()
        validate_plan(plan)

    def test_beta_validation_rejects_shared_fallback_intent(self):
        plan, _inventory = guided_plan()
        changed = replace(
            plan,
            boot=replace(plan.boot, install_fallback_path=True),
        )
        with self.assertRaisesRegex(
            ValueError, "must not write the shared EFI fallback path"
        ):
            validate_plan(changed)

    def test_small_extent_cannot_skip_esp_reuse(self):
        disk = windows_disk(free_gib=20.5)
        base = valid_plan()
        draft = replace(
            base,
            storage=replace(
                base.storage,
                mode=InstallMode.GUIDED_COEXISTENCE,
                disk=disk.identity,
                graph=None,
            ),
        )
        with self.assertRaisesRegex(ValueError, "requires a reusable ESP"):
            build_guided_coexistence_storage_graph(
                draft,
                disk,
                disk.free_extents[0],
                inventory_digest=INVENTORY_DIGEST,
                reused_esp=None,
            )


if __name__ == "__main__":
    unittest.main()
