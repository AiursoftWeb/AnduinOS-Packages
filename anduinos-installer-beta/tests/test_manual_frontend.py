import unittest
from dataclasses import replace
from unittest.mock import patch

from test_frontend import state
from test_manual_layout import (
    GIB,
    ManualPartitionRequest,
    ManualPartitionRole,
    manual_disk,
    selection,
)
from frontend import (
    FrontendPlanError,
    StorageStrategy,
    apply_storage_strategy,
    create_install_plan,
)
from installer_core.model import (
    Architecture,
    Firmware,
    InstallMode,
    SecureBoot,
)
from installer_core.probe import PlatformProbe
from installer_core.btrfs import BtrfsCompression
from installer_core.storage_inventory import StorageInventory
from installer_core.storage_ui import (
    build_manual_storage_preview,
    build_storage_workflow,
)


class ManualFrontendPlanTests(unittest.TestCase):
    def setUp(self):
        self.disk = manual_disk()
        self.inventory = StorageInventory((self.disk,), "e" * 64)
        self.platform = PlatformProbe(
            Architecture.AMD64,
            Firmware.UEFI,
            SecureBoot.ENABLED,
        )
        workflow = build_storage_workflow(
            self.inventory,
            self.platform,
            physical_memory_probe=lambda: 8 * 1024**3,
        )
        self.preview = build_manual_storage_preview(workflow, selection())
        self.values = state()
        self.values.update(
            {
                "disk": self.disk.identity.path,
                "disk_size_bytes": self.disk.identity.expected_size_bytes,
                "disk_stable_id": self.disk.identity.stable_id,
                "disk_topology_digest": self.disk.topology_digest,
                "disk_model": self.disk.identity.model,
                "storage_mode": InstallMode.MANUAL.value,
                "manual_storage_preview_model": self.preview,
            }
        )

    def test_strategy_maps_to_manual_without_mutating_a_disk(self):
        values = state()
        apply_storage_strategy(values, StorageStrategy.ADVANCED)
        self.assertEqual(values["storage_mode"], "manual")
        self.assertIsNone(values["manual_storage_preview_model"])

    def test_fresh_topology_rebuilds_the_manual_plan(self):
        old_preview = self.values["manual_storage_preview_model"]
        with patch("frontend.hash_password", return_value="$6$salt$hash"):
            plan = create_install_plan(
                self.values,
                inventory=self.inventory,
                platform=self.platform,
            )
        self.assertIs(plan.storage.mode, InstallMode.MANUAL)
        self.assertEqual(plan.storage.graph, old_preview.graph)
        self.assertEqual(plan.storage.swap_size_mib, 2561)
        self.assertFalse(plan.boot.install_fallback_path)
        self.assertIsNot(
            self.values["manual_storage_preview_model"],
            old_preview,
        )

    def test_below_minimum_manual_plan_does_not_use_automatic_swap_policy(self):
        small_disk = replace(
            self.disk,
            identity=replace(
                self.disk.identity,
                expected_size_bytes=23 * GIB,
            ),
            partition_table="",
            partition_table_uuid="",
            partitions=(),
            free_extents=(),
            geometry_probe_error="unrecognised disk label",
        )
        inventory = StorageInventory((small_disk,), "f" * 64)
        chosen = replace(
            selection(
                reinitialize=True,
                reused_esp="",
                new_partitions=(
                    ManualPartitionRequest(
                        ManualPartitionRole.EFI_SYSTEM, 1, 1025
                    ),
                    ManualPartitionRequest(
                        ManualPartitionRole.ROOT, 1025, 20 * 1024 + 1
                    ),
                    ManualPartitionRequest(
                        ManualPartitionRole.SWAP,
                        20 * 1024 + 1,
                        23 * 1024 - 210,
                    ),
                ),
            ),
            disk_size_bytes=small_disk.identity.expected_size_bytes,
        )
        workflow = build_storage_workflow(
            inventory,
            self.platform,
            physical_memory_probe=lambda: 8 * GIB,
        )
        preview = build_manual_storage_preview(workflow, chosen)
        values = {
            **self.values,
            "disk": small_disk.identity.path,
            "disk_size_bytes": small_disk.identity.expected_size_bytes,
            "disk_stable_id": small_disk.identity.stable_id,
            "disk_topology_digest": small_disk.topology_digest,
            "manual_storage_preview_model": preview,
        }

        with patch("frontend.hash_password", return_value="$6$salt$hash"):
            plan = create_install_plan(
                values,
                inventory=inventory,
                platform=self.platform,
            )

        self.assertIs(plan.storage.mode, InstallMode.MANUAL)
        self.assertEqual(19 * 1024, next(
            item.end_mib - item.start_mib
            for item in plan.storage.graph.partitions
            if item.name == "root"
        ))
        self.assertEqual(2861, plan.storage.swap_size_mib)

    def test_changed_manual_topology_is_rejected(self):
        changed_disk = replace(self.disk, topology_digest="f" * 64)
        changed_inventory = StorageInventory((changed_disk,), "f" * 64)
        with patch("frontend.hash_password", return_value="$6$salt$hash"):
            with self.assertRaisesRegex(FrontendPlanError, "topology changed"):
                create_install_plan(
                    self.values,
                    inventory=changed_inventory,
                    platform=self.platform,
                )

    def test_manual_plan_preserves_each_compression_choice(self):
        for preset in BtrfsCompression:
            with self.subTest(preset=preset), patch(
                "frontend.hash_password", return_value="$6$salt$hash"
            ):
                values = {**self.values, "btrfs_compression": preset.value}
                plan = create_install_plan(
                    values,
                    inventory=self.inventory,
                    platform=self.platform,
                )
                self.assertIs(plan.storage.mode, InstallMode.MANUAL)
                self.assertIs(plan.storage.btrfs_compression, preset)


if __name__ == "__main__":
    unittest.main()
