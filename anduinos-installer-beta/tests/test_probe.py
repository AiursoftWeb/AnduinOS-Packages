import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.probe import ProbeError, probe_disks, probe_platform


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class PlatformProbeTests(unittest.TestCase):
    def test_amd64_bios(self):
        with tempfile.TemporaryDirectory() as directory:
            result = probe_platform(
                machine="x86_64", efi_path=Path(directory) / "missing"
            )
        self.assertEqual(result.architecture, Architecture.AMD64)
        self.assertEqual(result.firmware, Firmware.BIOS)
        self.assertEqual(result.secure_boot, SecureBoot.NOT_APPLICABLE)

    def test_arm64_requires_uefi(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProbeError):
                probe_platform(
                    machine="aarch64", efi_path=Path(directory) / "missing"
                )

    def test_secure_boot_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            result = probe_platform(
                machine="aarch64",
                efi_path=Path(directory),
                run=lambda *args, **kwargs: completed("SecureBoot enabled"),
            )
        self.assertEqual(result.secure_boot, SecureBoot.ENABLED)


class DiskProbeTests(unittest.TestCase):
    def test_only_returns_stably_identified_fixed_whole_disks(self):
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/sda",
                    "size": 100_000_000_000,
                    "model": "Fixed",
                    "serial": "ABC",
                    "wwn": None,
                    "type": "disk",
                    "rm": False,
                },
                {
                    "path": "/dev/sdb",
                    "size": 20_000_000_000,
                    "model": "USB",
                    "serial": "USB",
                    "wwn": None,
                    "type": "disk",
                    "rm": True,
                },
                {
                    "path": "/dev/sda1",
                    "size": 1_000_000,
                    "model": "",
                    "serial": "",
                    "wwn": "",
                    "type": "part",
                    "rm": False,
                },
            ]
        }
        disks = probe_disks(
            run=lambda *args, **kwargs: completed(json.dumps(payload))
        )
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].stable_id, "serial:ABC")

