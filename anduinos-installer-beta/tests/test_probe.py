import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from installer_core.model import Architecture, Firmware, SecureBoot
from installer_core.probe import (
    ProbeError,
    _stable_disk_id,
    probe_disks,
    probe_platform,
)


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

    def test_uefi_without_secure_boot_support_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            result = probe_platform(
                machine="x86_64",
                efi_path=Path(directory),
                run=lambda *args, **kwargs: completed(
                    "This system doesn't support Secure Boot"
                ),
            )
        self.assertEqual(result.firmware, Firmware.UEFI)
        self.assertEqual(result.secure_boot, SecureBoot.UNSUPPORTED)

    def test_contradictory_secure_boot_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ProbeError, "unambiguous"):
                probe_platform(
                    machine="x86_64",
                    efi_path=Path(directory),
                    run=lambda *args, **kwargs: completed(
                        "SecureBoot enabled\n"
                        "This system doesn't support Secure Boot"
                    ),
                )

    def test_secure_boot_probe_forces_c_locale(self):
        captured = {}

        def run(*args, **kwargs):
            captured.update(kwargs)
            return completed("SecureBoot disabled")

        with tempfile.TemporaryDirectory() as directory:
            probe_platform(
                machine="x86_64", efi_path=Path(directory), run=run
            )
        self.assertEqual(captured["env"]["LC_ALL"], "C")


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
                    "maj:min": "8:0",
                },
                {
                    "path": "/dev/sdb",
                    "size": 20_000_000_000,
                    "model": "USB",
                    "serial": "USB",
                    "wwn": None,
                    "type": "disk",
                    "rm": True,
                    "maj:min": "8:16",
                },
                {
                    "path": "/dev/sda1",
                    "size": 1_000_000,
                    "model": "",
                    "serial": "",
                    "wwn": "",
                    "type": "part",
                    "rm": False,
                    "maj:min": "8:1",
                },
            ]
        }
        disks = probe_disks(
            run=lambda *args, **kwargs: completed(json.dumps(payload))
        )
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].stable_id, "serial:ABC")

    def test_serialless_virtio_disk_gets_live_session_identity(self):
        payload = {
            "blockdevices": [
                {
                    "path": "/dev/vda",
                    "size": 25 * 1024**3,
                    "model": "",
                    "serial": "",
                    "wwn": "",
                    "type": "disk",
                    "rm": False,
                    "maj:min": "253:0",
                },
                {
                    "path": "/dev/zram0",
                    "size": 3 * 1024**3,
                    "model": "",
                    "serial": "",
                    "wwn": "",
                    "type": "disk",
                    "rm": False,
                    "maj:min": "251:0",
                }
            ]
        }
        disks = probe_disks(
            run=lambda *args, **kwargs: completed(json.dumps(payload))
        )
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].path, "/dev/vda")
        self.assertEqual(disks[0].expected_size_bytes, 25 * 1024**3)
        self.assertTrue(
            disks[0].stable_id.startswith(("sysfs:", "kernel:/dev/vda|"))
        )
        self.assertIn("253:0", disks[0].stable_id)

    def test_by_path_precedes_session_only_kernel_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disk = root / "dev/vda"
            disk.parent.mkdir()
            disk.touch()
            by_path = root / "dev/disk/by-path"
            by_path.mkdir(parents=True)
            (by_path / "pci-test-virtio-pci").symlink_to(disk)
            identity = _stable_disk_id(
                str(disk),
                "",
                "",
                "253:0",
                by_id=root / "missing-by-id",
                by_path=by_path,
                sys_class_block=root / "missing-sysfs",
            )
        self.assertEqual(identity, "by-path:pci-test-virtio-pci")

    def test_kernel_fallback_requires_device_number(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            identity = _stable_disk_id(
                "/dev/vda",
                "",
                "",
                "",
                by_id=missing,
                by_path=missing,
                sys_class_block=missing,
            )
        self.assertEqual(identity, "")
