import json
import unittest
from dataclasses import replace

from helpers import valid_inventory, valid_plan
from installer_core.model import Filesystem, InstallPlan
from installer_core.storage_graph import (
    StorageCapability,
    StorageGraph,
    StorageGraphAction,
)
from installer_core.storage_graph_planning import (
    StorageGraphValidationError,
    resolve_storage_graph,
    validate_storage_graph,
)
from installer_core.storage_planning import build_erase_disk_execution_plan


class StorageGraphSchemaTests(unittest.TestCase):
    def test_round_trip_contains_no_device_path_or_command(self):
        graph = valid_plan().storage.graph
        self.assertIsNotNone(graph)
        value = graph.to_dict()
        encoded = json.dumps(value)
        self.assertNotIn("/dev/", encoded)
        self.assertNotIn("mkfs", encoded)
        self.assertNotIn("parted", encoded)
        self.assertEqual(StorageGraph.from_dict(value), graph)

    def test_strict_decoder_rejects_unknown_graph_field(self):
        value = valid_plan().to_dict()
        value["storage"]["graph"]["shell_hook"] = "do anything"
        with self.assertRaisesRegex(
            ValueError, "Unknown field in storage.graph"
        ):
            InstallPlan.from_dict(value)

    def test_strict_decoder_rejects_unknown_reference_field(self):
        value = valid_plan().to_dict()
        reference = value["storage"]["graph"]["block_references"][0]
        reference["device_path"] = "/dev/sda"
        with self.assertRaisesRegex(
            ValueError, "Unknown field in storage.graph.block_references"
        ):
            InstallPlan.from_dict(value)

    def test_btrfs_declares_roles_and_capabilities(self):
        graph = valid_plan().storage.graph
        self.assertEqual(
            graph.requested_capabilities,
            (
                StorageCapability.BOOTABLE,
                StorageCapability.SYSTEM_ROLLBACK,
                StorageCapability.WAYPOINT,
            ),
        )
        self.assertEqual(
            {item.target_path for item in graph.mounts},
            {
                "/",
                "/home",
                "/var/log",
                "/.snapshots",
                "/var/lib/containers",
                "/var/lib/libvirt/images",
                "/boot/efi",
            },
        )

    def test_ext4_declares_no_subvolumes_or_rollback(self):
        graph = valid_plan(filesystem=Filesystem.EXT4).storage.graph
        self.assertEqual(graph.subvolumes, ())
        self.assertEqual(
            graph.requested_capabilities,
            (StorageCapability.BOOTABLE,),
        )
        self.assertEqual(
            tuple(item.target_path for item in graph.mounts),
            ("/", "/boot/efi"),
        )

    def test_noncanonical_operation_order_is_rejected(self):
        plan = valid_plan()
        graph = plan.storage.graph
        changed = replace(
            graph,
            operations=tuple(reversed(graph.operations)),
        )
        changed_plan = replace(
            plan,
            storage=replace(plan.storage, graph=changed),
        )
        with self.assertRaisesRegex(
            StorageGraphValidationError, "canonical erase-disk plan"
        ):
            validate_storage_graph(changed_plan)

    def test_operations_are_identical_to_the_frozen_write_set(self):
        plan = valid_plan()
        execution = build_erase_disk_execution_plan(plan)
        graph_operations = tuple(
            (item.action.value, item.target_id)
            for item in plan.storage.graph.operations
        )
        write_operations = tuple(
            (item.action.value, item.target_id)
            for item in execution.write_set.operations
        )
        self.assertEqual(graph_operations, write_operations)
        self.assertEqual(
            graph_operations[0][0],
            StorageGraphAction.REPLACE_PARTITION_TABLE.value,
        )


class StorageGraphResolutionTests(unittest.TestCase):
    def test_resolves_a_changed_display_path_by_stable_identity(self):
        plan = valid_plan()
        inventory = valid_inventory(plan, path="/dev/nvme8n1")
        inventory = replace(inventory, digest="c" * 64)
        resolved = resolve_storage_graph(plan, inventory)
        self.assertEqual(resolved.storage.disk.path, "/dev/nvme8n1")
        self.assertEqual(resolved.storage.graph, plan.storage.graph)
        execution = build_erase_disk_execution_plan(resolved)
        all_commands = (
            *execution.commands.partition,
            *execution.commands.format,
        )
        self.assertTrue(
            all(
                "/dev/nvme0n1" not in argument
                for command in all_commands
                for argument in command
            )
        )
        self.assertTrue(
            any(
                "/dev/nvme8n1" in argument
                for command in all_commands
                for argument in command
            )
        )

    def test_rejects_a_stale_topology_digest(self):
        plan = valid_plan()
        inventory = valid_inventory(plan, topology_digest="c" * 64)
        with self.assertRaisesRegex(
            StorageGraphValidationError, "topology changed"
        ):
            resolve_storage_graph(plan, inventory)


if __name__ == "__main__":
    unittest.main()
