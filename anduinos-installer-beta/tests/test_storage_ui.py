import unittest
from dataclasses import replace

from test_coexistence import windows_disk
from installer_core.coexistence import (
    CoexistenceNoticeCode,
    CoexistenceStatus,
)
from installer_core.model import (
    Architecture,
    Filesystem,
    Firmware,
    SecureBoot,
)
from installer_core.probe import PlatformProbe
from installer_core.storage_inventory import StorageInventory
from installer_core.storage_ui import (
    GuidedStorageSelection,
    build_guided_storage_preview,
    build_guided_storage_confirmation,
    build_storage_workflow,
    recommended_guided_selection,
)
from installer_core.storage_write_set import StorageAction
from installer_core.swap_policy import GIB


INVENTORY_DIGEST = "e" * 64
PLATFORM = PlatformProbe(
    Architecture.AMD64,
    Firmware.UEFI,
    SecureBoot.ENABLED,
)


def workflow(*, free_gib=24, live_device=""):
    disk = windows_disk(free_gib=free_gib)
    inventory = StorageInventory((disk,), INVENTORY_DIGEST)
    return build_storage_workflow(
        inventory,
        PLATFORM,
        live_device=live_device,
        physical_memory_probe=lambda: 8 * GIB,
    )


class StorageWorkflowTests(unittest.TestCase):
    def test_recommended_preview_preserves_every_existing_partition(self):
        model = workflow()
        choice = model.disks[0]
        selection = recommended_guided_selection(
            choice, Filesystem.BTRFS
        )
        preview = build_guided_storage_preview(model, selection)

        self.assertEqual(selection.reused_esp_partuuid, "part-1")
        preserves = tuple(
            item
            for item in preview.write_set.operations
            if item.action is StorageAction.PRESERVE
        )
        self.assertEqual(len(preserves), len(choice.disk.partitions))
        formats = {
            item.display_path
            for item in preview.write_set.operations
            if item.action is StorageAction.FORMAT
        }
        self.assertNotIn(choice.disk.partitions[0].identity.path, formats)
        self.assertEqual(
            tuple(item.name for item in preview.graph.partitions),
            ("swap", "root"),
        )
        self.assertEqual(preview.swap_sizing.swap_size_mib, 3 * 1024)
        self.assertFalse(preview.swap_sizing.hibernation_capacity)

    def test_user_can_choose_a_new_esp_when_extent_is_large_enough(self):
        model = workflow()
        choice = model.disks[0]
        selected = recommended_guided_selection(choice, Filesystem.EXT4)
        selected = replace(selected, reused_esp_partuuid="")
        preview = build_guided_storage_preview(model, selected)

        self.assertIsNone(preview.reused_esp)
        self.assertEqual(
            tuple(item.name for item in preview.graph.partitions),
            ("efi-system", "swap", "root"),
        )
        self.assertEqual(preview.swap_sizing.swap_size_mib, 2 * 1024)
        self.assertFalse(preview.swap_sizing.hibernation_capacity)
        formats = {
            item.detail("filesystem")
            for item in preview.write_set.operations
            if item.action is StorageAction.FORMAT
        }
        self.assertEqual(formats, {"vfat", "swap", "ext4"})

    def test_confirmation_is_reduced_from_the_typed_write_set(self):
        model = workflow()
        selection = recommended_guided_selection(
            model.disks[0], Filesystem.BTRFS
        )
        preview = build_guided_storage_preview(model, selection)
        confirmation = build_guided_storage_confirmation(preview)

        self.assertEqual(
            confirmation.preserved_paths,
            tuple(item.identity.path for item in model.disks[0].disk.partitions),
        )
        self.assertEqual(
            tuple(item.name for item in confirmation.new_partitions),
            ("swap", "root"),
        )
        self.assertEqual(
            {item.filesystem for item in confirmation.formats},
            {"swap", "btrfs"},
        )
        self.assertEqual(confirmation.reused_esp_path, "/dev/nvme0n1p1")
        self.assertTrue(confirmation.writes_vendor_boot_files)
        self.assertFalse(confirmation.writes_shared_fallback)
        self.assertTrue(confirmation.updates_nvram)

    def test_missing_space_exposes_shrink_rescan_and_no_force_guidance(self):
        choice = workflow(free_gib=0).disks[0]
        self.assertEqual(
            choice.coexistence.status,
            CoexistenceStatus.ACTION_REQUIRED,
        )
        codes = {item.code for item in choice.coexistence.notices}
        self.assertIn(CoexistenceNoticeCode.SHRINK_IN_WINDOWS, codes)
        self.assertIn(CoexistenceNoticeCode.RESCAN_AFTER_CHANGES, codes)
        self.assertIn(CoexistenceNoticeCode.NO_FORCE_CONTINUE, codes)
        with self.assertRaisesRegex(ValueError, "not available"):
            recommended_guided_selection(choice, Filesystem.BTRFS)

    def test_live_media_can_be_inspected_but_never_selected(self):
        model = workflow(live_device="/dev/nvme0n1")
        choice = model.disks[0]
        self.assertTrue(choice.is_live_media)
        self.assertFalse(choice.erase_available)
        self.assertFalse(choice.guided_available)

    def test_stale_extent_or_esp_selection_is_rejected(self):
        model = workflow()
        selection = recommended_guided_selection(
            model.disks[0], Filesystem.BTRFS
        )
        cases = (
            replace(selection, free_extent_id="missing"),
            replace(selection, reused_esp_partuuid="missing"),
        )
        for stale in cases:
            with self.subTest(selection=stale):
                with self.assertRaisesRegex(ValueError, "changed"):
                    build_guided_storage_preview(model, stale)

    def test_selection_binds_disk_size_as_well_as_stable_id(self):
        model = workflow()
        selection = recommended_guided_selection(
            model.disks[0], Filesystem.BTRFS
        )
        changed = GuidedStorageSelection(
            disk_stable_id=selection.disk_stable_id,
            disk_size_bytes=selection.disk_size_bytes + 1,
            free_extent_id=selection.free_extent_id,
            reused_esp_partuuid=selection.reused_esp_partuuid,
            filesystem=selection.filesystem,
        )
        with self.assertRaisesRegex(ValueError, "size changed"):
            build_guided_storage_preview(model, changed)


if __name__ == "__main__":
    unittest.main()
