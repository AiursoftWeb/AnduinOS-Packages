"""Storage coverage follows the active filesystems, including mixed layouts."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from anduinos_driver_center.encryption import encryption_state


class InventoryRunner:
    def __init__(self, filesystem="btrfs"):
        self.blocks = [
            dict(name="/dev/vda", type="disk"),
            dict(name="/dev/vda1", type="part", fstype="crypto_LUKS", uuid="outer", pkname="/dev/vda"),
            dict(name="/dev/mapper/arbitrary", type="crypt", fstype=filesystem, uuid="root", pkname="/dev/vda1"),
            dict(name="/dev/vda2", type="part", fstype="swap", uuid="swap", pkname="/dev/vda"),
            dict(name="/dev/vda3", type="part", fstype="ext4", uuid="home", pkname="/dev/vda"),
        ]
        root = dict(source="/dev/mapper/arbitrary[/@root]", fstype=filesystem, uuid="root")
        self.mounts = {"/": root, "/home": root}
        self.swaps = []
        self.block_failure = False
        self.swap_failure = False
        self.commands = []

    def run(self, args):
        self.commands.append(args)
        if args[0] == "lsblk":
            code, value = int(self.block_failure), {"blockdevices": self.blocks}
        elif args[0] == "findmnt":
            code, value = 0, {"filesystems": [self.mounts[args[-1]]]}
        else:
            raise AssertionError(args)
        return SimpleNamespace(returncode=code, stdout=json.dumps(value), stderr="")

    def active_swaps(self):
        if self.swap_failure:
            raise OSError("Swap metadata unavailable")
        return self.swaps


def inspect(runner):
    with patch("anduinos_driver_center.encryption.active_swaps", runner.active_swaps):
        return encryption_state(runner)


class EncryptionTests(unittest.TestCase):
    def test_btrfs_subvolumes_share_the_actual_crypt_device(self):
        runner = InventoryRunner()
        state = inspect(runner)
        self.assertTrue(state.root.encrypted)
        self.assertTrue(state.home.encrypted)
        self.assertEqual(state.root.filesystem, "btrfs")
        self.assertEqual(state.disk_swap, "none")
        self.assertEqual({command[0] for command in runner.commands}, {"lsblk", "findmnt"})

    def test_encrypted_ext4_does_not_imply_a_separate_home_is_encrypted(self):
        runner = InventoryRunner("ext4")
        runner.mounts["/home"] = dict(source="/dev/vda3", fstype="ext4", uuid="home")
        state = inspect(runner)
        self.assertTrue(state.root.encrypted)
        self.assertFalse(state.home.encrypted)

    def test_plain_partition_is_a_valid_unencrypted_installation(self):
        runner = InventoryRunner("ext4")
        runner.blocks[2].update(name="/dev/vda4", type="part", pkname="/dev/vda")
        state = inspect(runner)
        self.assertFalse(state.root.encrypted)
        self.assertFalse(state.home.encrypted)

    def test_duplicate_filesystem_uuid_cannot_prove_complete_btrfs_coverage(self):
        runner = InventoryRunner()
        runner.blocks.append(dict(name="/dev/vdb", type="disk", fstype="btrfs", uuid="root"))
        self.assertIsNone(inspect(runner).root.encrypted)

    def test_missing_inventory_and_live_overlay_remain_unknown(self):
        runner = InventoryRunner()
        runner.block_failure = True
        self.assertIsNone(inspect(runner).root.encrypted)
        runner.block_failure = False
        runner.mounts["/"] = dict(source="overlay", fstype="overlay", uuid=None)
        state = inspect(runner)
        self.assertIsNone(state.root.encrypted)
        self.assertEqual(state.root.filesystem, "overlay")

    def test_swap_mixed_encryption_and_zram_are_distinguished(self):
        runner = InventoryRunner()
        runner.blocks.append(dict(name="/dev/mapper/random-swap", type="crypt", fstype="swap", pkname="/dev/vda2"))
        runner.swaps = [dict(name="/dev/zram0", type="partition")]
        self.assertEqual(inspect(runner).disk_swap, "none")
        runner.swaps.append(dict(name="/dev/mapper/random-swap", type="partition"))
        self.assertEqual(inspect(runner).disk_swap, "encrypted")
        runner.swaps.append(dict(name="/dev/vda2", type="partition"))
        self.assertEqual(inspect(runner).disk_swap, "plaintext")
        runner.swap_failure = True
        self.assertEqual(inspect(runner).disk_swap, "unknown")

    def test_swapfile_follows_its_own_mount_not_the_encrypted_root(self):
        runner = InventoryRunner()
        runner.mounts["/home/swapfile"] = dict(source="/dev/vda3", fstype="ext4", uuid="home")
        runner.swaps = [dict(name="/home/swapfile", type="file")]
        self.assertEqual(inspect(runner).disk_swap, "plaintext")

    def test_swapfile_with_spaces_uses_the_existing_kernel_escape_reader(self):
        runner = InventoryRunner()
        runner.mounts["/swap with spaces"] = runner.mounts["/"]
        proc = "Filename\tType\tSize\tUsed\tPriority\n/swap\\040with\\040spaces file 1024 0 -2\n"
        with patch("pathlib.Path.read_text", return_value=proc):
            state = encryption_state(runner)
        self.assertEqual(state.disk_swap, "encrypted")

    def test_incomplete_and_cyclic_parent_chains_remain_unknown(self):
        runner = InventoryRunner("ext4")
        runner.blocks[2].update(type="lvm", pkname="/dev/missing")
        self.assertIsNone(inspect(runner).root.encrypted)
        runner.blocks[2]["pkname"] = "/dev/mapper/arbitrary"
        self.assertIsNone(inspect(runner).root.encrypted)


if __name__ == "__main__":
    unittest.main()
