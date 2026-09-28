"""Exercise real Parted against a disposable regular file, without root/NTFS."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer_core.command import CommandRunner
from installer_core.ntfs_resize import NTFS_RESIZE
from installer_core.storage_commands import NtfsResizeCommandPlan
from installer_core.storage_steps import PrepareStorageStep


@unittest.skipUnless(shutil.which("parted") and shutil.which("sfdisk"),
                     "real partition shrink regression requires parted and sfdisk")
class PartedShrinkTests(unittest.TestCase):
    def test_executor_confirms_shrink_and_preserves_partition_identity(self):
        with tempfile.TemporaryDirectory(prefix="anduinos-parted-shrink-") as directory:
            image = Path(directory) / "disk.img"
            with image.open("wb") as stream:
                stream.truncate(128 * 1024**2)
            real = CommandRunner(lambda _message: None)
            real.run(("parted", "--script", str(image), "mklabel", "gpt",
                      "mkpart", "Windows", "ntfs", "1MiB", "100MiB",
                      "mkpart", "Recovery", "ntfs", "100MiB", "120MiB"), timeout=10)

            def table():
                result = real.run(("sfdisk", "--json", str(image)), timeout=10)
                return json.loads(result.stdout)["partitiontable"]

            before = table()
            resize = NtfsResizeCommandPlan(
                target_reference_id="windows", disk=str(image),
                device="not-a-real-ntfs-device", partition_number=1,
                original_size_bytes=99 * 1024**2,
                target_size_bytes=49 * 1024**2,
                target_end_bytes=50 * 1024**2 - 1,
            )
            # Reproduce the original regression first. Providing stdin cannot
            # make --script accept this warning, so the boundary stays intact.
            rejected = real.run(
                ("parted", "--script", str(image), "unit", "B", "resizepart", "1",
                 f"{resize.target_end_bytes}B"),
                input_text="Yes\n", environment=dict(os.environ, LC_ALL="C"),
                check=False, timeout=10,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Shrinking a partition", rejected.stdout + rejected.stderr)
            self.assertEqual(table(), before)

            commands = []

            def run(command, **options):
                commands.append(tuple(command))
                if command[0] == "parted":
                    self.assertIn(str(image), command)
                    return real.run(command, **{**options, "timeout": 10})
                # No NTFS volume, real block device, sync, or host partition
                # rescan is involved in this focused Parted regression.
                self.assertIn(command[0], {NTFS_RESIZE, "sync", "partprobe", "udevadm"})
                return subprocess.CompletedProcess(command, 0, "", "")

            step = PrepareStorageStep(SimpleNamespace(run=run))
            plan = SimpleNamespace(commands=SimpleNamespace(ntfs_resizes=(resize,)))
            context = SimpleNamespace(log=lambda _message: None)
            with patch("installer_core.storage_steps.inspect_ntfs_resize_with_runner",
                       return_value=SimpleNamespace(safe=True)), \
                    patch.object(step, "_verify_resized_partition_geometry") as verify:
                step._execute_ntfs_resizes(context, plan)
                verify.assert_called_once_with(context, resize)
            after = table()
            expected = dict(before["partitions"][0], size=resize.target_size_bytes // 512)
            self.assertEqual(after["partitions"][0], expected)
            self.assertEqual(after["partitions"][1:], before["partitions"][1:])
            self.assertEqual(after["id"], before["id"])
            self.assertLess(next(i for i, cmd in enumerate(commands) if cmd[0] == NTFS_RESIZE),
                            next(i for i, cmd in enumerate(commands) if cmd[0] == "parted"))
