"""Real tools on disposable sparse files; no sudo, loop devices or host mounts.

This verifies filesystem data and GPT preservation, not OS bootability. ISO/VM
acceptance remains a separate gate before shipping.
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer_core.command import CommandRunner
from installer_core.ext4_resize import inspect_ext4_resize, DUMPE2FS, parse_ext4_geometry
from installer_core.storage_commands import PartitionResizeCommandPlan
from installer_core.storage_graph import GraphFilesystem
from installer_core.storage_steps import PrepareStorageStep

MIB = 1024**2
GIB = 1024**3
TOOLS = ("mke2fs", "e2fsck", "resize2fs", "dumpe2fs", "debugfs", "parted", "sfdisk", "dd")


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS),
                     "real ext4/GPT regression requires e2fsprogs, parted, sfdisk and dd")
class Ext4ShrinkImageTests(unittest.TestCase):
    def test_real_executor_shrinks_ext4_preserves_files_uuid_gpt_and_neighbor(self):
        with tempfile.TemporaryDirectory(prefix="anduinos-ext4-shrink-") as directory:
            root = Path(directory)
            filesystem, disk = root / "ext4.img", root / "disk.img"
            for path, size in ((filesystem, 8 * GIB), (disk, 12 * GIB)):
                with path.open("wb") as stream:
                    stream.truncate(size)
            seed = root / "seed"
            (seed / "etc").mkdir(parents=True)
            (seed / "boot").mkdir()
            content = bytes(range(256)) * 4096
            (seed / "etc" / "important-data").write_bytes(content)
            (seed / "etc" / "config-link").symlink_to("important-data")
            (seed / "boot" / "boot-sentinel").write_text("old installation survives\n")
            real = CommandRunner(lambda message: None)
            real.run(("mke2fs", "-q", "-t", "ext4", "-b", "4096", "-d", str(seed),
                      str(filesystem)), timeout=120)
            real.run(("parted", "--script", str(disk), "mklabel", "gpt", "mkpart",
                      "OldLinux", "ext4", "1MiB", "8193MiB", "mkpart", "Neighbor",
                      "ext4", "10240MiB", "11264MiB"), timeout=10)
            def table():
                return json.loads(real.run(("sfdisk", "--json", str(disk)), timeout=10).stdout)["partitiontable"]
            before = table()
            before_fs = parse_ext4_geometry(real.run((DUMPE2FS, "-h", str(filesystem))).stdout)
            with disk.open("r+b") as stream:
                stream.seek(10 * GIB)
                stream.write(content)
            real.run(("dd", f"if={filesystem}", f"of={disk}", "bs=1M", "seek=1",
                      "count=8192", "conv=notrunc,sparse", "status=none"), timeout=120)
            assessment = inspect_ext4_resize(str(filesystem), 8 * GIB,
                                             target_size_bytes=4 * GIB)
            self.assertTrue(assessment.safe, assessment.message)
            commands = []
            def run(command, **options):
                commands.append(tuple(command))
                if command[0] in {"partprobe", "udevadm"}:
                    # Regular files are not kernel block devices.
                    import subprocess
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[0] == "parted":
                    # The production executor works in-place on a partition
                    # block device. For this rootless test only, copy the
                    # resized filesystem into its original disk-file offset.
                    geometry = parse_ext4_geometry(real.run((DUMPE2FS, "-h", str(filesystem))).stdout)
                    self.assertLessEqual(geometry.size_bytes, 4 * GIB)
                    real.run(("dd", f"if={filesystem}", f"of={disk}", "bs=1M", "seek=1",
                              "count=4096", "conv=notrunc", "status=none"), timeout=120)
                return real.run(command, **options)
            resize = PartitionResizeCommandPlan("old-root", str(disk), str(filesystem),
                1, 8 * GIB, 4 * GIB, MIB + 4 * GIB - 1, GraphFilesystem.EXT4)
            step = PrepareStorageStep(SimpleNamespace(run=run))
            execution = SimpleNamespace(commands=SimpleNamespace(partition_resizes=(resize,)))
            context = SimpleNamespace(log=lambda message: None)
            with patch.object(step, "_verify_ext4_resize_target", return_value=before_fs.uuid), \
                    patch.object(step, "_verify_resized_partition_geometry"):
                step._execute_partition_resizes(context, execution)
            after = table()
            self.assertEqual(before["id"], after["id"])
            self.assertEqual(after["partitions"][0],
                             dict(before["partitions"][0], size=4 * GIB // 512))
            self.assertEqual(after["partitions"][1:], before["partitions"][1:])
            # Re-read actual disk bytes inside the now-smaller partition,
            # not merely the intermediate filesystem image.
            extracted = root / "extracted.img"
            real.run(("dd", f"if={disk}", f"of={extracted}", "bs=1M", "skip=1",
                      "count=4096", "conv=sparse", "status=none"), timeout=120)
            real.run(("e2fsck", "-f", "-n", str(extracted)), timeout=120)
            after_fs = parse_ext4_geometry(real.run((DUMPE2FS, "-h", str(extracted))).stdout)
            self.assertEqual(after_fs.uuid, before_fs.uuid)
            data = root / "data-after"
            real.run(("debugfs", "-R", f"dump /etc/important-data {data}", str(extracted)), timeout=10)
            self.assertTrue(data.exists())
            self.assertEqual(hashlib.sha256(data.read_bytes()).digest(), hashlib.sha256(content).digest())
            link = real.run(("debugfs", "-R", "stat /etc/config-link", str(extracted)), timeout=10)
            self.assertIn("important-data", link.stdout)
            boot = real.run(("debugfs", "-R", "cat /boot/boot-sentinel", str(extracted)), timeout=10)
            self.assertIn("old installation survives", boot.stdout)
            # Allocate a new partition in the released tail and verify both
            # old identities and the neighboring sentinel still survive.
            real.run(("parted", "--script", str(disk), "mkpart", "NewLinux", "ext4",
                      "4097MiB", "7169MiB"), timeout=10)
            final = table()
            self.assertEqual(final["partitions"][:2], after["partitions"][:2])
            with disk.open("rb") as stream:
                stream.seek(10 * GIB)
                self.assertEqual(stream.read(len(content)), content)
            self.assertLess(next(i for i, cmd in enumerate(commands) if cmd[0].endswith("resize2fs") and "-P" not in cmd),
                            next(i for i, cmd in enumerate(commands) if cmd[0] == "parted"))
