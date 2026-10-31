import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anduinos_rescue_center.model import Partition
from anduinos_rescue_center.snapshots import ENGINE, create_snapshot, restore_snapshot
from anduinos_rescue_center.storage import SystemIdentity


def partition(*, filesystem="btrfs", mountpoints=()):
    return Partition(
        path="/dev/sda2",
        parent="/dev/sda",
        size_bytes=1000,
        filesystem=filesystem,
        filesystem_uuid="uuid",
        label="",
        partuuid="partuuid",
        mountpoints=mountpoints,
        identity="a" * 64,
        active_system=False,
    )


class SnapshotBridgeTests(unittest.TestCase):
    def test_restore_protects_before_replacing_root(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps({"schema": 1}), "")

        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory) / "repair-test"
            top.mkdir()
            with (
                patch("anduinos_rescue_center.snapshots.resolve_target", return_value=partition()),
                patch("anduinos_rescue_center.snapshots.mounted_writable") as mounted,
                patch(
                    "anduinos_rescue_center.snapshots.inspect_mounted_filesystem",
                    return_value=SystemIdentity(os_kind="anduinos", btrfs_layout=True),
                ),
            ):
                mounted.return_value.__enter__.return_value = top
                restore_snapshot(
                    "/dev/sda2",
                    "a" * 64,
                    "11111111-1111-4111-8111-111111111111",
                    True,
                    run=run,
                )
        self.assertEqual([call[1] for call in calls], ["resume", "protect", "restore"])
        self.assertTrue(all(call[0] == ENGINE for call in calls))

    def test_create_rejects_control_characters_before_mount(self):
        with self.assertRaises(ValueError):
            create_snapshot("/dev/sda2", "a" * 64, "bad\nname")

    def test_non_btrfs_target_is_rejected(self):
        with patch(
            "anduinos_rescue_center.snapshots.resolve_target",
            return_value=partition(filesystem="ext4"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Btrfs"):
                create_snapshot("/dev/sda2", "a" * 64, "Safe point")

    def test_mounted_target_must_be_unmounted_before_mutation(self):
        with patch(
            "anduinos_rescue_center.snapshots.resolve_target",
            return_value=partition(mountpoints=("/mnt",)),
        ):
            with self.assertRaisesRegex(RuntimeError, "Unmount"):
                create_snapshot("/dev/sda2", "a" * 64, "Safe point")
