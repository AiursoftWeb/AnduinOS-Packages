"""The To Go entry must reject a non-USB ISO before overlay creation."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "dracut/95anduinos-live-layers/anduinos-create-overlay.sh"
MESSAGE = (
    "AnduinOS To Go requires a USB drive written in DD mode with "
    "unallocated space after the image. This boot medium is not supported."
)


class ToGoGuardTests(unittest.TestCase):
    def test_wrapper_has_valid_shell_syntax(self) -> None:
        subprocess.run(["sh", "-n", str(WRAPPER)], check=True)

    def test_hybrid_mbr_and_gpt_media_are_both_accepted(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('case "$partition_table" in', source)
        self.assertIn('dos) return 0 ;;', source)
        self.assertIn('gpt) ;;', source)

    def test_non_partition_media_is_rejected_with_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory)
            commands = {
                # Rufus-style rewrites must not bypass the To Go media guard.
                "getargbool": "exit 1\n",
                "getarg": "printf '%s\\n' LABEL=ANDUINOS-PERSIST\n",
                "warn": "printf '%s\\n' \"$*\" >&2\n",
                "die": "printf '%s\\n' \"$*\" >&2\n",
                "plymouth": "exit 1\n",
            }
            for name, body in commands.items():
                command = fake_bin / name
                command.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                command.chmod(0o755)
            result = subprocess.run(
                ["sh", str(WRAPPER), "/dev/null"],
                env={**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn(MESSAGE, result.stderr)
            self.assertNotIn("create-overlay.upstream", result.stderr)

    def test_existing_overlay_must_be_on_the_boot_disk(self) -> None:
        source = WRAPPER.read_text(encoding="utf-8")
        self.assertIn('readlink -f /dev/disk/by-label/ANDUINOS-PERSIST', source)
        self.assertIn('[ "$overlay_parent_sysfs" = "$parent_sysfs" ]', source)


if __name__ == "__main__":
    unittest.main()
