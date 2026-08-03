from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_driver_center.core import (  # noqa: E402
    normalize_key,
    dkms_state,
    parse_ubuntu_driver_devices,
    secure_boot_state,
    xbox_state,
)


class FakeRunner:
    def __init__(self, responses=None, installed=()):
        self.responses = responses or {}
        self.installed = set(installed)

    def run(self, command, timeout=10):
        command = tuple(command)
        if command[:3] == ("dpkg-query", "-W", "-f=${db:Status-Abbrev}"):
            package = command[3]
            return subprocess.CompletedProcess(
                command,
                0 if package in self.installed else 1,
                "ii " if package in self.installed else "",
                "",
            )
        return self.responses.get(
            command, subprocess.CompletedProcess(command, 1, "", "")
        )


class CoreTests(unittest.TestCase):
    def test_normalizes_certificate_and_module_key_formats(self):
        self.assertEqual(normalize_key("AB:12 cd 34"), "ab12cd34")
        self.assertIsNone(normalize_key("---"))

    def test_parses_ubuntu_drivers_device_and_recommendation(self):
        output = """== /sys/devices/pci0000:00/0000:01:00.0 ==
modalias : pci:v000010DEd00002820
vendor   : NVIDIA Corporation
model    : AD107M [GeForce RTX 4060 Max-Q]
driver   : nvidia-driver-590 - distro non-free recommended
driver   : xserver-xorg-video-nouveau - distro free builtin
"""
        devices = parse_ubuntu_driver_devices(
            output, FakeRunner(installed={"nvidia-driver-590"})
        )
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].vendor, "NVIDIA Corporation")
        self.assertEqual(devices[0].model, "AD107M [GeForce RTX 4060 Max-Q]")
        self.assertEqual(devices[0].title, "NVIDIA GeForce RTX 4060 Max-Q")
        self.assertTrue(devices[0].options[0].recommended)
        self.assertTrue(devices[0].options[0].installed)
        self.assertTrue(devices[0].options[1].free)
        self.assertTrue(devices[0].options[1].builtin)

    def test_secure_boot_requires_key_certificate_and_enrollment(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "MOK.priv"
            certificate = Path(directory) / "MOK.der"
            private.write_text("private")
            certificate.write_text("certificate")
            responses = {
                ("mokutil", "--sb-state"): subprocess.CompletedProcess([], 0, "SecureBoot enabled\n", ""),
                ("mokutil", "--test-key", str(certificate)): subprocess.CompletedProcess([], 0, "MOK.der is already enrolled\n", ""),
                ("openssl", "x509", "-in", str(certificate), "-inform", "DER", "-noout", "-serial"): subprocess.CompletedProcess([], 0, "serial=AA12BB34\n", ""),
            }
            state = secure_boot_state(FakeRunner(responses), private, certificate)
            self.assertTrue(state.ready)
            self.assertEqual(state.certificate_serial, "aa12bb34")

    def test_xbox_detects_signature_mismatch_as_secure_boot_block(self):
        from anduinos_driver_center.core import SecureBootState

        secure = SecureBootState(True, True, True, True, "aa12")
        responses = {
            ("modinfo", "hid-xpadneo"): subprocess.CompletedProcess([], 0, "sig_key: BB:34\n", ""),
            ("lsmod",): subprocess.CompletedProcess([], 0, "hid_xpadneo 40960 0\n", ""),
        }
        state = xbox_state(
            secure,
            FakeRunner(responses, installed={"anduinos-xbox-controller-driver"}),
        )
        self.assertTrue(state.installed)
        self.assertTrue(state.module_loaded)
        self.assertFalse(state.signature_matches)
        self.assertTrue(state.blocked_by_secure_boot)

    def test_dkms_health_reports_modules_signed_by_a_different_key(self):
        from anduinos_driver_center.core import SecureBootState

        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "example.ko.zst"
            module.write_text("module")
            secure = SecureBootState(True, True, True, True, "aa12")
            responses = {
                ("modinfo", str(module)): subprocess.CompletedProcess(
                    [], 0, "sig_key: BB:34\n", ""
                ),
            }
            state = dkms_state(secure, FakeRunner(responses), Path(directory))
            self.assertEqual(state.modules, ("example.ko.zst",))
            self.assertEqual(state.untrusted_modules, ("example.ko.zst",))
            self.assertFalse(state.ready)


if __name__ == "__main__":
    unittest.main()
