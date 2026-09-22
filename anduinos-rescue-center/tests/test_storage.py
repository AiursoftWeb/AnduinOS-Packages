import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anduinos_rescue_center.storage import (
    SystemIdentity,
    inspect_offline_root,
    inspect_partition,
    inspect_mounted_filesystem,
    probe_inventory,
)
from anduinos_rescue_center.model import Partition


class StorageDiscoveryTests(unittest.TestCase):
    def test_discovers_anduinos_and_preserves_active_root_boundary(self):
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/nvme0n1", "type": "disk", "size": 1000,
                    "model": "Fast Disk", "serial": "abc", "maj:min": "259:0",
                    "children": [
                        {
                            "path": "/dev/nvme0n1p2", "type": "part", "size": 900,
                            "fstype": "btrfs", "uuid": "fs", "partuuid": "part",
                            "maj:min": "259:2", "mountpoints": ["/"],
                        }
                    ],
                }
            ]
        }

        def run(command, **_kwargs):
            if command[0] == "lsblk":
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            return subprocess.CompletedProcess(command, 0, "SecureBoot enabled\n", "")

        inventory = probe_inventory(
            run=run,
            inspect=lambda _path, _fstype: SystemIdentity(
                "anduinos", "AnduinOS 2.0.3", "2.0.3", "workstation", True
            ),
            live=False,
        )
        found = inventory.installations[0]
        self.assertTrue(found.active_system)
        self.assertTrue(found.btrfs_layout)
        self.assertEqual(found.hostname, "workstation")
        self.assertEqual(inventory.secure_boot, "enabled")

    def test_one_unreadable_partition_does_not_hide_other_installations(self):
        payload = {
            "blockdevices": [{
                "path": "/dev/sda", "type": "disk", "size": 2000, "maj:min": "8:0",
                "children": [
                    {"path": "/dev/sda1", "type": "part", "size": 1000,
                     "fstype": "ext4", "maj:min": "8:1", "mountpoints": []},
                    {"path": "/dev/sda2", "type": "part", "size": 1000,
                     "fstype": "btrfs", "maj:min": "8:2", "mountpoints": []},
                ],
            }]
        }

        def run(command, **_kwargs):
            output = json.dumps(payload) if command[0] == "lsblk" else "SecureBoot disabled"
            return subprocess.CompletedProcess(command, 0, output, "")

        def inspect(path, _filesystem):
            if path.endswith("1"):
                raise OSError("damaged filesystem")
            return SystemIdentity("anduinos", "AnduinOS", "2.0.3", "pc", True)

        inventory = probe_inventory(run=run, inspect=inspect, live=True)
        self.assertEqual(len(inventory.installations), 1)
        self.assertIn("damaged filesystem", inventory.disks[0].partitions[0].probe_error)


class MountedFilesystemTests(unittest.TestCase):
    @staticmethod
    def partition(filesystem="btrfs"):
        return Partition(
            path="/dev/test1", parent="/dev/test", size_bytes=1000,
            filesystem=filesystem, filesystem_uuid="fs", label="",
            partuuid="part", mountpoints=(), identity="a" * 64,
        )

    def test_requires_positive_anduinos_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "@root"
            (root / "etc").mkdir(parents=True)
            (root / "etc/os-release").write_text(
                'ID=anduinos\nPRETTY_NAME="AnduinOS 2.0.3"\nVERSION_ID=2.0.3\n',
                encoding="utf-8",
            )
            (root / "etc/hostname").write_text("rescue-me\n", encoding="utf-8")
            found = inspect_mounted_filesystem(Path(directory), "btrfs")
            self.assertEqual(found.os_kind, "anduinos")
            self.assertTrue(found.btrfs_layout)
            self.assertEqual(found.hostname, "rescue-me")

    def test_ntfs_is_not_called_windows_without_windows_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            found = inspect_mounted_filesystem(Path(directory), "ntfs")
            self.assertEqual(found.os_kind, "unknown")

    def test_windows_requires_system_and_program_data_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Windows/System32").mkdir(parents=True)
            (root / "ProgramData").mkdir()
            found = inspect_mounted_filesystem(root, "ntfs")
            self.assertEqual(found.os_kind, "windows")

    def test_selected_system_lists_only_root_and_local_human_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "@root"
            (root / "etc").mkdir(parents=True)
            (root / "boot").mkdir()
            (root / "usr/lib").mkdir(parents=True)
            (root / "usr/lib/os-release").write_text(
                'ID=anduinos\nPRETTY_NAME="AnduinOS 2.0.3"\nVERSION_ID=2.0.3\n',
                encoding="utf-8",
            )
            (root / "etc/passwd").write_text(
                "root:x:0:0:root:/root:/bin/bash\n"
                "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
                "alice:x:1000:1000:Alice Example:/home/alice:/bin/bash\n",
                encoding="utf-8",
            )
            (root / "etc/shadow").write_text(
                "root:!:1:0:99999:7:::\nalice:$hash:1:0:99999:7:::\n",
                encoding="utf-8",
            )
            (root / "boot/vmlinuz-7.0.0-test").touch()
            payload = inspect_offline_root(Path(directory), self.partition())
            self.assertEqual([user["name"] for user in payload["users"]], ["root", "alice"])
            self.assertTrue(payload["users"][0]["locked"])
            self.assertFalse(payload["users"][1]["locked"])
            self.assertEqual(payload["system"]["kernels"], ["7.0.0-test"])

    def test_os_identity_symlink_cannot_escape_mounted_system(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            (top / "etc").mkdir()
            (top / "etc/os-release").symlink_to("/etc/os-release")
            found = inspect_mounted_filesystem(top, "ext4")
            self.assertEqual(found.os_kind, "unknown")

    def test_btrfs_probe_disables_log_replay_and_unmounts_its_own_mount(self):
        calls = []

        def run(command, **_kwargs):
            calls.append(command)
            if command[0] == "mount":
                root = Path(command[-1]) / "@root/etc"
                root.mkdir(parents=True)
                (root / "os-release").write_text("ID=anduinos\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            mount_base = Path(directory) / "mounts"
            with patch("anduinos_rescue_center.storage._require_block_device"):
                found = inspect_partition(
                    "/dev/test1", "btrfs", run=run, mount_base=mount_base
                )
        self.assertEqual(found.os_kind, "anduinos")
        self.assertIn("ro,nologreplay,subvolid=5", calls[0])
        self.assertEqual(calls[-1][0], "umount")

    def test_unmount_failure_is_reported_without_recursive_cleanup(self):
        mountpoint = None

        def run(command, **_kwargs):
            nonlocal mountpoint
            if command[0] == "mount":
                mountpoint = Path(command[-1])
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "busy")

        with tempfile.TemporaryDirectory() as directory:
            mount_base = Path(directory) / "mounts"
            with patch("anduinos_rescue_center.storage._require_block_device"):
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    inspect_partition(
                        "/dev/test1", "ext4", run=run, mount_base=mount_base
                    )
            self.assertIsNotNone(mountpoint)
            self.assertTrue(mountpoint.exists())
            mountpoint.rmdir()
