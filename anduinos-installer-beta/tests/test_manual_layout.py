import unittest
from dataclasses import replace

from installer_core.manual_layout import (
    MIB,
    MICROSOFT_LDM_DATA_GUID,
    ManualLayoutError,
    ManualPartitionRequest,
    ManualPartitionResizeRequest,
    ManualPartitionRole,
    ManualStorageSelection,
    manual_available_extents,
    validate_manual_selection,
)
from installer_core.model import DiskIdentity, Filesystem
from installer_core.storage_inventory import (
    EFI_SYSTEM_PARTITION_GUID,
    DiskInventory,
    FreeExtent,
    PartitionIdentity,
    PartitionInventory,
)


GIB = 1024 * MIB
DISK_ID = "serial:manual-test"
TOPOLOGY = "a" * 64


def existing_partition(
    number,
    start_mib,
    end_mib,
    *,
    filesystem="ext4",
    partition_type="linux",
    mountpoints=(),
):
    return PartitionInventory(
        identity=PartitionIdentity(
            path=f"/dev/nvme0n1p{number}",
            number=number,
            partuuid=f"part-{number}",
            start_bytes=start_mib * MIB,
            size_bytes=(end_mib - start_mib) * MIB,
        ),
        parent_disk_id=DISK_ID,
        partition_type=partition_type,
        filesystem_type=filesystem,
        filesystem_uuid=f"fs-{number}",
        mountpoints=mountpoints,
        flags=("esp",) if partition_type == EFI_SYSTEM_PARTITION_GUID else (),
    )


def manual_disk():
    return DiskInventory(
        identity=DiskIdentity(
            "/dev/nvme0n1",
            DISK_ID,
            128 * GIB,
            "Manual Test SSD",
            "MANUAL-1",
        ),
        partition_table="gpt",
        partition_table_uuid="manual-gpt",
        partitions=(
            existing_partition(
                1,
                1,
                1025,
                filesystem="vfat",
                partition_type=EFI_SYSTEM_PARTITION_GUID,
            ),
            existing_partition(2, 1025, 80 * 1024),
        ),
        free_extents=(
            FreeExtent(
                DISK_ID,
                80 * GIB,
                (128 * 1024 - 1 - 80 * 1024) * MIB,
            ),
        ),
        topology_digest=TOPOLOGY,
    )


def selection(
    *,
    reinitialize=False,
    deleted=(),
    reused_esp="part-1",
    filesystem=Filesystem.BTRFS,
    new_partitions=None,
    resized=(),
):
    disk = manual_disk()
    return ManualStorageSelection(
        disk_stable_id=DISK_ID,
        disk_size_bytes=disk.identity.expected_size_bytes,
        disk_topology_digest=TOPOLOGY,
        reinitialize_gpt=reinitialize,
        deleted_partuuids=deleted,
        reused_esp_partuuid=reused_esp,
        filesystem=filesystem,
        new_partitions=(
            (
                ManualPartitionRequest(
                    ManualPartitionRole.ROOT,
                    80 * 1024,
                    110 * 1024,
                ),
                ManualPartitionRequest(
                    ManualPartitionRole.SWAP,
                    110 * 1024,
                    112 * 1024 + 513,
                ),
            )
            if new_partitions is None
            else new_partitions
        ),
        resized_partitions=resized,
    )


class ManualLayoutTests(unittest.TestCase):
    def test_reuses_esp_and_accepts_arbitrary_mib_partition_sizes(self):
        chosen = selection()
        validate_manual_selection(manual_disk(), chosen)
        swap = next(
            item
            for item in chosen.new_partitions
            if item.role is ManualPartitionRole.SWAP
        )
        self.assertEqual(swap.size_mib, 2561)

    def test_deleted_partition_becomes_allocatable_without_resizing(self):
        chosen = selection(
            deleted=("part-2",),
            new_partitions=(),
        )
        extents = manual_available_extents(manual_disk(), chosen)
        self.assertEqual(extents[0].start_mib, 1025)
        self.assertEqual(extents[0].end_mib, 128 * 1024 - 1)

    def test_ntfs_shrink_exposes_only_the_partition_tail(self):
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
        chosen = selection(new_partitions=(), resized=(request,))
        validate_manual_selection(
            disk,
            selection(
                new_partitions=(
                    ManualPartitionRequest(
                        ManualPartitionRole.ROOT,
                        1025 + 40 * 1024,
                        1025 + 60 * 1024,
                    ),
                ),
                resized=(request,),
            ),
        )
        extents = manual_available_extents(disk, chosen)
        self.assertEqual(extents[0].start_mib, 1025 + 40 * 1024)
        self.assertEqual(extents[0].end_mib, 128 * 1024 - 1)

    def test_resize_rejects_delete_bitlocker_and_invalid_target(self):
        disk = manual_disk()
        ntfs = replace(disk.partitions[1], filesystem_type="ntfs")
        disk = replace(disk, partitions=(disk.partitions[0], ntfs))
        valid = ManualPartitionResizeRequest(
            "part-2", ntfs.identity.size_bytes, 40 * 1024
        )
        cases = (
            (selection(deleted=("part-2",), resized=(valid,)), "resized and deleted"),
            (
                selection(
                    resized=(
                        ManualPartitionResizeRequest(
                            "part-2", ntfs.identity.size_bytes, 1024
                        ),
                    )
                ),
                "requested NTFS size",
            ),
        )
        for chosen, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ManualLayoutError, message):
                    validate_manual_selection(disk, chosen)

        bitlocker_disk = replace(
            disk,
            partitions=(
                disk.partitions[0],
                replace(ntfs, filesystem_type="bitlocker"),
            ),
        )
        with self.assertRaises(ManualLayoutError):
            validate_manual_selection(bitlocker_disk, selection(resized=(valid,)))

    def test_reinitialized_gpt_requires_new_esp_and_root(self):
        chosen = selection(
            reinitialize=True,
            reused_esp="",
            new_partitions=(
                ManualPartitionRequest(ManualPartitionRole.EFI_SYSTEM, 1, 1025),
                ManualPartitionRequest(ManualPartitionRole.ROOT, 1025, 30 * 1024),
            ),
        )
        validate_manual_selection(manual_disk(), chosen)
        self.assertEqual(
            manual_available_extents(manual_disk(), chosen),
            (
                # The remainder of the replacement GPT stays unallocated.
                type(manual_available_extents(manual_disk(), chosen)[0])(
                    30 * 1024,
                    128 * 1024 - 1,
                ),
            ),
        )

    def test_missing_roles_overlap_and_small_root_fail_closed(self):
        cases = (
            (
                selection(new_partitions=()),
                "exactly one Root",
            ),
            (
                selection(
                    new_partitions=(
                        ManualPartitionRequest(ManualPartitionRole.ROOT, 80 * 1024, 105 * 1024),
                        ManualPartitionRequest(ManualPartitionRole.SWAP, 100 * 1024, 110 * 1024),
                    )
                ),
                "ordered by geometry|overlap",
            ),
            (
                selection(
                    new_partitions=(
                        ManualPartitionRequest(ManualPartitionRole.ROOT, 80 * 1024, 90 * 1024),
                    )
                ),
                "at least 20 GiB",
            ),
        )
        for chosen, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ManualLayoutError, message):
                    validate_manual_selection(manual_disk(), chosen)

    def test_existing_esp_is_never_deleted_or_reused_after_reinitialization(self):
        cases = (
            selection(deleted=("part-1",)),
            selection(reinitialize=True),
        )
        for chosen in cases:
            with self.subTest(selection=chosen):
                with self.assertRaisesRegex(
                    ManualLayoutError,
                    "EFI|reinitialization",
                ):
                    validate_manual_selection(manual_disk(), chosen)

    def test_unsupported_or_active_storage_is_rejected(self):
        disk = manual_disk()
        cases = (
            replace(
                disk,
                partitions=(
                    disk.partitions[0],
                    replace(disk.partitions[1], filesystem_type="bitlocker"),
                ),
            ),
            replace(
                disk,
                partitions=(
                    disk.partitions[0],
                    replace(disk.partitions[1], filesystem_type="crypto_LUKS"),
                ),
            ),
            replace(disk, unsupported_descendant_types=("lvm",)),
            replace(
                disk,
                partitions=(
                    disk.partitions[0],
                    replace(
                        disk.partitions[1],
                        partition_type=MICROSOFT_LDM_DATA_GUID,
                    ),
                ),
            ),
            replace(
                disk,
                partitions=(
                    disk.partitions[0],
                    replace(disk.partitions[1], mountpoints=("/media/data",)),
                ),
            ),
        )
        for blocked in cases:
            with self.subTest(disk=blocked):
                with self.assertRaises(ManualLayoutError):
                    validate_manual_selection(blocked, selection())


if __name__ == "__main__":
    unittest.main()
