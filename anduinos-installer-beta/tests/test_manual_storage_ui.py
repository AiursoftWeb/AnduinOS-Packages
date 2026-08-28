import unittest
from unittest.mock import patch

from test_manual_layout import manual_disk, selection
from pages import _manual_segment_spans, _probe_storage_workflow
from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.manual_layout import (
    ManualPartitionRequest,
    ManualPartitionResizeRequest,
    ManualPartitionRole,
)
from installer_core.probe import PlatformProbe
from installer_core.storage_inventory import StorageInventory
from installer_core.storage_ui import (
    ManualDiskSegmentKind,
    build_development_storage_workflow,
    build_manual_disk_map,
    build_manual_storage_confirmation,
    build_manual_storage_preview,
    build_storage_workflow,
)


class ManualStorageUiTests(unittest.TestCase):
    def setUp(self):
        self.disk = manual_disk()
        self.inventory = StorageInventory((self.disk,), "e" * 64)
        self.platform = PlatformProbe(
            Architecture.AMD64,
            Firmware.UEFI,
            SecureBoot.ENABLED,
        )
        self.workflow = build_storage_workflow(
            self.inventory,
            self.platform,
            physical_memory_probe=lambda: 8 * 1024**3,
        )

    def test_preview_and_confirmation_are_reduced_from_the_same_graph(self):
        preview = build_manual_storage_preview(
            self.workflow,
            selection(),
        )
        confirmation = build_manual_storage_confirmation(preview)
        self.assertEqual(
            confirmation.preserved_paths,
            ("/dev/nvme0n1p1", "/dev/nvme0n1p2"),
        )
        self.assertEqual(confirmation.deleted_paths, ())
        self.assertEqual(confirmation.resized_partitions, ())
        self.assertEqual(
            tuple(item.name for item in confirmation.new_partitions),
            ("root", "swap"),
        )
        self.assertEqual(
            {item.filesystem for item in confirmation.formats},
            {"btrfs", "swap"},
        )
        self.assertEqual(confirmation.reused_esp_path, "/dev/nvme0n1p1")
        self.assertFalse(confirmation.reinitializes_gpt)
        self.assertFalse(confirmation.writes_shared_fallback)
        self.assertTrue(confirmation.updates_nvram)

    def test_live_medium_and_legacy_bios_are_rejected(self):
        live = build_storage_workflow(
            self.inventory,
            self.platform,
            live_device=self.disk.identity.path,
            physical_memory_probe=lambda: 8 * 1024**3,
        )
        with self.assertRaisesRegex(ValueError, "Live medium"):
            build_manual_storage_preview(live, selection())

        bios = build_storage_workflow(
            self.inventory,
            PlatformProbe(
                Architecture.AMD64,
                Firmware.BIOS,
                SecureBoot.NOT_APPLICABLE,
            ),
            physical_memory_probe=lambda: 8 * 1024**3,
        )
        with self.assertRaisesRegex(ValueError, "requires UEFI"):
            build_manual_storage_preview(bios, selection())

    def test_development_workflow_uses_only_a_synthetic_editable_disk(self):
        workflow = build_development_storage_workflow(self.platform)
        self.assertEqual(len(workflow.disks), 1)
        choice = workflow.disks[0]
        self.assertEqual(choice.disk.identity.path, "/dev/vda")
        self.assertIn("simulated", choice.disk.identity.model)
        self.assertEqual(choice.disk.partition_table, "gpt")
        self.assertEqual(len(choice.disk.partitions), 3)
        self.assertEqual(len(choice.disk.free_extents), 1)
        self.assertTrue(choice.guided_available)
        self.assertFalse(choice.is_live_media)

    def test_development_probe_never_reads_real_storage_inventory(self):
        with (
            patch("pages.probe_platform", return_value=self.platform),
            patch("pages.probe_storage_inventory") as real_probe,
        ):
            workflow = _probe_storage_workflow(development_mode=True)
        real_probe.assert_not_called()
        self.assertEqual(
            workflow.disks[0].disk.identity.stable_id,
            "serial:anduinos-development-disk",
        )

    def test_disk_map_distinguishes_current_deletes_and_planned_layout(self):
        chosen = selection(deleted=("part-2",), new_partitions=())
        disk_map = build_manual_disk_map(self.disk, chosen)
        self.assertEqual(
            tuple(item.kind for item in disk_map.current),
            (
                ManualDiskSegmentKind.PRESERVED,
                ManualDiskSegmentKind.DELETED,
                ManualDiskSegmentKind.FREE,
            ),
        )
        self.assertEqual(
            tuple(item.kind for item in disk_map.planned),
            (
                ManualDiskSegmentKind.PRESERVED,
                ManualDiskSegmentKind.FREE,
            ),
        )

    def test_reinitialized_disk_map_contains_only_new_and_free_segments(self):
        from installer_core.manual_layout import (
            ManualPartitionRequest,
            ManualPartitionRole,
        )

        chosen = selection(
            reinitialize=True,
            reused_esp="",
            new_partitions=(
                ManualPartitionRequest(
                    ManualPartitionRole.EFI_SYSTEM, 1, 1025
                ),
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT, 1025, 40 * 1024
                ),
            ),
        )
        disk_map = build_manual_disk_map(self.disk, chosen)
        self.assertTrue(
            all(
                item.kind is ManualDiskSegmentKind.DELETED
                for item in disk_map.current
                if item.kind is not ManualDiskSegmentKind.FREE
            )
        )
        self.assertEqual(
            tuple(item.kind for item in disk_map.planned[:2]),
            (
                ManualDiskSegmentKind.NEW_ESP,
                ManualDiskSegmentKind.NEW_ROOT,
            ),
        )

    def test_disk_map_shows_resized_ntfs_and_its_new_free_tail(self):
        from dataclasses import replace

        disk = replace(
            self.disk,
            partitions=(
                self.disk.partitions[0],
                replace(self.disk.partitions[1], filesystem_type="ntfs"),
            ),
        )
        request = ManualPartitionResizeRequest(
            "part-2",
            disk.partitions[1].identity.size_bytes,
            40 * 1024,
        )
        disk_map = build_manual_disk_map(
            disk,
            selection(new_partitions=(), resized=(request,)),
        )
        resized = next(
            item
            for item in disk_map.planned
            if item.kind is ManualDiskSegmentKind.RESIZED
        )
        self.assertEqual(resized.end_mib, 1025 + 40 * 1024)
        self.assertTrue(
            any(
                item.kind is ManualDiskSegmentKind.FREE
                and item.start_mib == resized.end_mib
                for item in disk_map.planned
            )
        )

    def test_confirmation_exposes_the_exact_ntfs_resize(self):
        from dataclasses import replace

        disk = replace(
            self.disk,
            partitions=(
                self.disk.partitions[0],
                replace(self.disk.partitions[1], filesystem_type="ntfs"),
            ),
        )
        workflow = build_storage_workflow(
            StorageInventory((disk,), "f" * 64),
            self.platform,
            physical_memory_probe=lambda: 8 * 1024**3,
        )
        original = disk.partitions[1].identity.size_bytes
        target_mib = 40 * 1024
        preview = build_manual_storage_preview(
            workflow,
            selection(
                new_partitions=(
                    ManualPartitionRequest(
                        ManualPartitionRole.ROOT,
                        1025 + target_mib,
                        70 * 1024,
                    ),
                ),
                resized=(
                    ManualPartitionResizeRequest(
                        "part-2", original, target_mib
                    ),
                ),
            ),
        )
        confirmation = build_manual_storage_confirmation(preview)
        self.assertEqual(len(confirmation.resized_partitions), 1)
        resized = confirmation.resized_partitions[0]
        self.assertEqual(resized.display_path, "/dev/nvme0n1p2")
        self.assertEqual(resized.original_size_bytes, original)
        self.assertEqual(resized.target_size_bytes, target_mib * 1024**2)
        self.assertEqual(
            resized.reclaimed_bytes,
            original - target_mib * 1024**2,
        )

    def test_disk_map_spans_fill_one_bounded_track(self):
        disk_map = build_manual_disk_map(self.disk, selection())
        columns, spans = _manual_segment_spans(disk_map.current)
        self.assertEqual(sum(spans), columns)
        self.assertEqual(len(spans), len(disk_map.current))
        self.assertTrue(all(item >= 6 for item in spans))


if __name__ == "__main__":
    unittest.main()
