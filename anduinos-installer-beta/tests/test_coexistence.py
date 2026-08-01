import unittest
from dataclasses import replace

from installer_core.coexistence import (
    GIB,
    CoexistenceBlocker,
    CoexistenceNoticeCode,
    CoexistenceStatus,
    analyze_guided_coexistence,
)
from installer_core.model import DiskIdentity, Firmware
from installer_core.storage_inventory import (
    EFI_SYSTEM_PARTITION_GUID,
    MICROSOFT_BASIC_DATA_PARTITION_GUID,
    WINDOWS_RECOVERY_PARTITION_GUID,
    DiskInventory,
    FreeExtent,
    PartitionIdentity,
    PartitionInventory,
)


DISK_ID = "serial:windows-test"


def partition(
    number,
    *,
    size_gib,
    partition_type,
    filesystem,
    partuuid=None,
    filesystem_uuid=None,
    mountpoints=(),
):
    return PartitionInventory(
        identity=PartitionIdentity(
            path=f"/dev/nvme0n1p{number}",
            number=number,
            partuuid=partuuid or f"part-{number}",
            start_bytes=number * GIB,
            size_bytes=int(size_gib * GIB),
        ),
        parent_disk_id=DISK_ID,
        partition_type=partition_type,
        filesystem_type=filesystem,
        filesystem_uuid=filesystem_uuid or f"fs-{number}",
        mountpoints=mountpoints,
        flags=("esp",) if partition_type == EFI_SYSTEM_PARTITION_GUID else (),
    )


def windows_disk(*, free_gib=0, mounted=False, bitlocker=False):
    esp = partition(
        1,
        size_gib=0.25,
        partition_type=EFI_SYSTEM_PARTITION_GUID,
        filesystem="vfat",
    )
    windows = partition(
        3,
        size_gib=80,
        partition_type=MICROSOFT_BASIC_DATA_PARTITION_GUID,
        filesystem="bitlocker" if bitlocker else "ntfs",
        mountpoints=("/media/Windows",) if mounted else (),
    )
    recovery = partition(
        4,
        size_gib=1,
        partition_type=WINDOWS_RECOVERY_PARTITION_GUID,
        filesystem="ntfs",
    )
    extents = (
        (FreeExtent(DISK_ID, 90 * GIB, int(free_gib * GIB)),)
        if free_gib
        else ()
    )
    return DiskInventory(
        identity=DiskIdentity(
            "/dev/nvme0n1",
            DISK_ID,
            128 * GIB,
            "Windows SSD",
            "WIN-1",
        ),
        partition_table="gpt",
        partition_table_uuid="windows-gpt",
        partitions=(esp, windows, recovery),
        free_extents=extents,
        topology_digest="a" * 64,
    )


def notice_codes(decision):
    return {item.code for item in decision.notices}


class GuidedCoexistenceEligibilityTests(unittest.TestCase):
    def test_available_extent_preserves_windows_and_never_resizes(self):
        decision = analyze_guided_coexistence(
            windows_disk(free_gib=24),
            Firmware.UEFI,
        )
        self.assertEqual(decision.status, CoexistenceStatus.AVAILABLE)
        self.assertTrue(decision.can_install_from_free_space)
        self.assertTrue(decision.windows_detected)
        self.assertEqual(len(decision.free_space_candidates), 1)
        self.assertFalse(
            decision.free_space_candidates[0].requires_reused_esp
        )
        codes = notice_codes(decision)
        self.assertIn(
            CoexistenceNoticeCode.USES_UNALLOCATED_SPACE_ONLY,
            codes,
        )
        self.assertIn(
            CoexistenceNoticeCode.PRESERVES_EXISTING_PARTITIONS,
            codes,
        )
        self.assertIn(
            CoexistenceNoticeCode.WINDOWS_STATE_NOT_REPAIRED,
            codes,
        )
        text = " ".join(item.message for item in decision.notices)
        self.assertIn("will not shrink or move", text)
        self.assertIn("hibernation or Fast Startup", text)

    def test_missing_free_space_requires_windows_shrink_without_override(self):
        decision = analyze_guided_coexistence(
            windows_disk(),
            Firmware.UEFI,
        )
        self.assertEqual(decision.status, CoexistenceStatus.ACTION_REQUIRED)
        self.assertFalse(decision.can_install_from_free_space)
        self.assertIn(
            CoexistenceBlocker.NO_SUITABLE_FREE_SPACE,
            decision.blockers,
        )
        codes = notice_codes(decision)
        self.assertIn(CoexistenceNoticeCode.SHRINK_IN_WINDOWS, codes)
        self.assertIn(CoexistenceNoticeCode.NO_FORCE_CONTINUE, codes)
        self.assertIn(CoexistenceNoticeCode.RESCAN_AFTER_CHANGES, codes)
        text = " ".join(item.message for item in decision.notices)
        self.assertIn("Windows Disk Management", text)
        self.assertIn("There is no force-continue option", text)

    def test_disposable_partition_is_explicit_and_never_preselected(self):
        decision = analyze_guided_coexistence(
            windows_disk(),
            Firmware.UEFI,
        )
        self.assertEqual(len(decision.disposable_partition_candidates), 1)
        candidate = decision.disposable_partition_candidates[0]
        self.assertEqual(candidate.identity.number, 3)
        self.assertFalse(decision.can_install_from_free_space)
        self.assertIn(
            CoexistenceNoticeCode.DISPOSABLE_PARTITION_OPTION,
            notice_codes(decision),
        )
        self.assertNotIn(
            4,
            {
                item.identity.number
                for item in decision.disposable_partition_candidates
            },
        )

    def test_bitlocker_is_detected_but_never_unlocked_or_resized(self):
        decision = analyze_guided_coexistence(
            windows_disk(free_gib=24, bitlocker=True),
            Firmware.UEFI,
        )
        self.assertTrue(decision.bitlocker_detected)
        self.assertIn(
            CoexistenceNoticeCode.BITLOCKER_NOT_MODIFIED,
            notice_codes(decision),
        )
        text = " ".join(item.message for item in decision.notices)
        self.assertIn("not unlock, resize, repair", text)

    def test_existing_esp_can_enable_a_smaller_free_extent(self):
        with_esp = analyze_guided_coexistence(
            windows_disk(free_gib=20.5),
            Firmware.UEFI,
        )
        self.assertTrue(with_esp.can_install_from_free_space)
        self.assertTrue(
            with_esp.free_space_candidates[0].requires_reused_esp
        )

        disk = windows_disk(free_gib=20.5)
        without_esp = replace(
            disk,
            partitions=tuple(
                item
                for item in disk.partitions
                if not item.is_efi_system_partition
            ),
        )
        decision = analyze_guided_coexistence(without_esp, Firmware.UEFI)
        self.assertFalse(decision.can_install_from_free_space)

    def test_mounted_partition_requires_unmount_and_rescan(self):
        decision = analyze_guided_coexistence(
            windows_disk(free_gib=24, mounted=True),
            Firmware.UEFI,
        )
        self.assertEqual(decision.status, CoexistenceStatus.ACTION_REQUIRED)
        self.assertIn(CoexistenceBlocker.DISK_IN_USE, decision.blockers)
        self.assertIn(
            CoexistenceNoticeCode.UNMOUNT_AND_RESCAN,
            notice_codes(decision),
        )

    def test_non_gpt_or_legacy_boot_is_unsupported(self):
        disk = replace(windows_disk(free_gib=24), partition_table="msdos")
        decision = analyze_guided_coexistence(disk, Firmware.BIOS)
        self.assertEqual(decision.status, CoexistenceStatus.UNSUPPORTED)
        self.assertFalse(decision.can_install_from_free_space)
        self.assertIn(CoexistenceBlocker.NON_GPT_DISK, decision.blockers)
        self.assertIn(CoexistenceBlocker.LEGACY_FIRMWARE, decision.blockers)
        self.assertIn(
            CoexistenceNoticeCode.UEFI_GPT_REQUIRED,
            notice_codes(decision),
        )
        self.assertNotIn(
            CoexistenceNoticeCode.SHRINK_IN_WINDOWS,
            notice_codes(decision),
        )

    def test_incomplete_geometry_or_nested_mapping_is_unsupported(self):
        disk = replace(
            windows_disk(free_gib=24),
            geometry_probe_error="parted mismatch",
            unsupported_descendant_types=("crypt",),
        )
        decision = analyze_guided_coexistence(disk, Firmware.UEFI)
        self.assertEqual(decision.status, CoexistenceStatus.UNSUPPORTED)
        self.assertIn(
            CoexistenceBlocker.INCOMPLETE_GEOMETRY,
            decision.blockers,
        )
        self.assertIn(
            CoexistenceBlocker.UNSUPPORTED_MAPPING,
            decision.blockers,
        )
        codes = notice_codes(decision)
        self.assertIn(CoexistenceNoticeCode.GEOMETRY_UNAVAILABLE, codes)
        self.assertIn(CoexistenceNoticeCode.MAPPING_UNSUPPORTED, codes)

    def test_missing_partition_identity_is_unsupported(self):
        disk = windows_disk(free_gib=24)
        unidentified = replace(
            disk.partitions[1],
            identity=replace(disk.partitions[1].identity, partuuid=""),
        )
        disk = replace(
            disk,
            partitions=(disk.partitions[0], unidentified, disk.partitions[2]),
        )
        decision = analyze_guided_coexistence(disk, Firmware.UEFI)
        self.assertEqual(decision.status, CoexistenceStatus.UNSUPPORTED)
        self.assertIn(
            CoexistenceBlocker.UNSTABLE_IDENTITIES,
            decision.blockers,
        )
        self.assertIn(
            CoexistenceNoticeCode.IDENTITY_UNAVAILABLE,
            notice_codes(decision),
        )


if __name__ == "__main__":
    unittest.main()
