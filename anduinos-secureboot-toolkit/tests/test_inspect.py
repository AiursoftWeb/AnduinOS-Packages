from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_secureboot.inspect import (  # noqa: E402
    certificate_enrolled,
    inspect_dkms,
    inspect_secure_boot,
    normalize_key,
    parse_secure_boot_status,
)
from anduinos_secureboot.model import SecureBootStatus  # noqa: E402


class FakeRunner:
    def __init__(self, responses=None):
        self.responses = responses or {}

    def run(self, command, timeout=10):
        command = tuple(command)
        return self.responses.get(
            command, subprocess.CompletedProcess(command, 1, "", "")
        )


class InspectTests(unittest.TestCase):
    def test_normalizes_certificate_keys(self):
        self.assertEqual(normalize_key("AA:12 bb"), "aa12bb")
        self.assertIsNone(normalize_key("---"))

    def test_parses_every_explicit_mokutil_state(self):
        cases = (
            ("SecureBoot enabled", SecureBootStatus.ENABLED),
            ("Secure Boot disabled", SecureBootStatus.DISABLED),
            (
                "This system doesn't support Secure Boot",
                SecureBootStatus.UNSUPPORTED,
            ),
            (
                "This system does not support Secure Boot",
                SecureBootStatus.UNSUPPORTED,
            ),
        )
        for output, expected in cases:
            with self.subTest(output=output):
                result = subprocess.CompletedProcess([], 0, output, "")
                self.assertEqual(parse_secure_boot_status(result), expected)

    def test_failed_or_contradictory_probe_is_unknown(self):
        cases = (
            subprocess.CompletedProcess([], 127, "", "mokutil not found"),
            subprocess.CompletedProcess(
                [],
                0,
                "SecureBoot enabled\nSecureBoot disabled",
                "",
            ),
        )
        for result in cases:
            with self.subTest(output=(result.stdout, result.stderr)):
                self.assertEqual(
                    parse_secure_boot_status(result),
                    SecureBootStatus.UNKNOWN,
                )

    def test_unsupported_firmware_is_not_treated_as_probe_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = inspect_secure_boot(
                FakeRunner(
                    {
                        ("mokutil", "--sb-state"): subprocess.CompletedProcess(
                            [], 0, "This system doesn't support Secure Boot\n", ""
                        )
                    }
                ),
                root / "missing.priv",
                root / "missing.der",
                "test-kernel",
                root / "missing.conf",
            )
        self.assertEqual(state.status, SecureBootStatus.UNSUPPORTED)
        self.assertFalse(state.supported)
        self.assertTrue(state.state_known)
        self.assertTrue(state.enforcement_inactive)
        self.assertTrue(state.ready)

    def test_indeterminate_probe_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "unsigned.ko"
            module.write_text("module")
            state = inspect_secure_boot(
                FakeRunner(),
                root / "missing.priv",
                root / "missing.der",
                "test-kernel",
                root / "missing.conf",
            )
            dkms = inspect_dkms(
                state,
                FakeRunner(
                    {
                        ("modinfo", "-F", "sig_key", str(module)):
                            subprocess.CompletedProcess([], 0, "", "")
                    }
                ),
                root,
            )
        self.assertEqual(state.status, SecureBootStatus.UNKNOWN)
        self.assertFalse(state.supported)
        self.assertFalse(state.state_known)
        self.assertFalse(state.ready)
        self.assertEqual(dkms.untrusted_modules, ("unsigned.ko",))

    def test_enrolled_certificate_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            configuration = root / "anduinos-sb-sign.conf"
            private.write_text("private")
            certificate.write_text("certificate")
            configuration.write_text("configuration")
            responses = {
                ("mokutil", "--sb-state"): subprocess.CompletedProcess([], 0, "SecureBoot enabled\n", ""),
                ("mokutil", "--list-enrolled"): subprocess.CompletedProcess([], 0, "SHA1 Fingerprint: aa:12\n", ""),
                ("mokutil", "--list-new"): subprocess.CompletedProcess([], 0, "", ""),
                ("openssl", "x509", "-in", str(certificate), "-inform", "DER", "-noout", "-serial"): subprocess.CompletedProcess([], 0, "serial=AA12\n", ""),
                ("openssl", "x509", "-in", str(certificate), "-inform", "DER", "-noout", "-fingerprint", "-sha1"): subprocess.CompletedProcess([], 0, "sha1 Fingerprint=AA:12\n", ""),
            }
            state = inspect_secure_boot(
                FakeRunner(responses), private, certificate, "test-kernel", configuration
            )
            self.assertTrue(state.ready)
            self.assertTrue(state.trust_ready)
            self.assertTrue(state.enrolled)
            self.assertEqual(state.certificate_serial, "aa12")

    def test_mokutil_072_exit_one_still_means_enrolled(self):
        certificate = Path("/test/MOK.der")
        responses = {
            ("mokutil", "--test-key", str(certificate)): subprocess.CompletedProcess(
                [], 1, f"{certificate} is already enrolled\n", ""
            ),
        }
        self.assertTrue(certificate_enrolled(certificate, FakeRunner(responses)))

    def test_missing_dkms_config_does_not_erase_firmware_trust(self):
        from anduinos_secureboot.model import SecureBootState

        state = SecureBootState(
            enabled=True,
            key_present=True,
            certificate_present=True,
            enrolled=True,
            certificate_serial="aa12",
            configuration_present=False,
        )
        self.assertTrue(state.trust_ready)
        self.assertFalse(state.ready)
        self.assertFalse(state.enrollment_required)

    def test_disabled_secure_boot_needs_no_key_config_or_module_signature(self):
        from anduinos_secureboot.model import SecureBootState

        secure_boot = SecureBootState(
            enabled=False,
            key_present=False,
            certificate_present=False,
            enrolled=False,
            certificate_serial=None,
            configuration_present=False,
        )
        self.assertTrue(secure_boot.trust_ready)
        self.assertTrue(secure_boot.ready)
        self.assertFalse(secure_boot.enrollment_required)

        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "unsigned.ko"
            module.write_text("module")
            state = inspect_dkms(
                secure_boot,
                FakeRunner(
                    {
                        ("modinfo", "-F", "sig_key", str(module)):
                            subprocess.CompletedProcess([], 0, "", "")
                    }
                ),
                Path(directory),
            )
        self.assertEqual(state.trusted_modules, ("unsigned.ko",))
        self.assertTrue(state.ready)

    def test_empty_pending_mok_list_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            configuration = root / "anduinos-sb-sign.conf"
            private.write_text("private")
            certificate.write_text("certificate")
            configuration.write_text("configuration")
            responses = {
                ("mokutil", "--sb-state"): subprocess.CompletedProcess([], 0, "SecureBoot enabled\n", ""),
                ("mokutil", "--list-enrolled"): subprocess.CompletedProcess([], 0, "", ""),
                ("mokutil", "--list-new"): subprocess.CompletedProcess([], 0, "", ""),
                ("openssl", "x509", "-in", str(certificate), "-inform", "DER", "-noout", "-serial"): subprocess.CompletedProcess([], 0, "serial=AA12\n", ""),
                ("openssl", "x509", "-in", str(certificate), "-inform", "DER", "-noout", "-fingerprint", "-sha1"): subprocess.CompletedProcess([], 0, "sha1 Fingerprint=AA:12\n", ""),
            }
            state = inspect_secure_boot(
                FakeRunner(responses), private, certificate, "test-kernel", configuration
            )
            self.assertFalse(state.enrolled)
            self.assertFalse(state.enrollment_pending)
            self.assertFalse(state.trust_ready)

    def test_dkms_reports_signature_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root / "example.ko.zst"
            module.write_text("module")
            from anduinos_secureboot.model import SecureBootState

            secure_boot = SecureBootState(True, True, True, True, "aa12")
            responses = {
                ("modinfo", "-F", "sig_key", str(module)): subprocess.CompletedProcess([], 0, "BB:34\n", "")
            }
            state = inspect_dkms(secure_boot, FakeRunner(responses), root)
            self.assertEqual(state.modules, ("example.ko.zst",))
            self.assertEqual(state.untrusted_modules, ("example.ko.zst",))


if __name__ == "__main__":
    unittest.main()
