import unittest

from anduinos_rescue_center.selection import can_open_partition


class RecoverySelectionTests(unittest.TestCase):
    def test_btrfs_and_ext4_offline_targets_can_be_opened(self):
        disk = {"live_medium": False}
        for filesystem in ("btrfs", "ext4"):
            with self.subTest(filesystem=filesystem):
                self.assertTrue(can_open_partition(disk, {
                    "path": "/dev/vda2", "identity": "bound-device",
                    "filesystem": filesystem, "os_kind": "anduinos",
                }))

    def test_running_live_unbound_and_windows_targets_are_not_offered(self):
        target = {"path": "/dev/vda2", "identity": "bound-device",
                  "filesystem": "ext4", "os_kind": "anduinos"}
        self.assertFalse(can_open_partition({"live_medium": True}, target))
        self.assertFalse(can_open_partition({}, target | {"active_system": True}))
        self.assertFalse(can_open_partition({}, target | {"identity": ""}))
        self.assertFalse(can_open_partition({}, target | {"os_kind": "windows"}))
        self.assertFalse(can_open_partition({}, target | {"filesystem": "ntfs"}))
