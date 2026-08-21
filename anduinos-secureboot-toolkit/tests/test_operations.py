from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_secureboot import operations  # noqa: E402


def with_secure_boot_enabled(run):
    def wrapped(command, **kwargs):
        if list(command) == ["mokutil", "--sb-state"]:
            return subprocess.CompletedProcess(
                command, 0, "SecureBoot enabled\n", ""
            )
        return run(command, **kwargs)

    return wrapped


class OperationsTests(unittest.TestCase):
    def test_prepare_skips_known_non_enforcing_firmware_states(self):
        for output in (
            "SecureBoot disabled\n",
            "This system doesn't support Secure Boot\n",
        ):
            with self.subTest(output=output):
                calls = []

                def run(command, **kwargs):
                    calls.append(list(command))
                    return subprocess.CompletedProcess(command, 0, output, "")

                result = operations.prepare(run)
                self.assertTrue(result.ok)
                self.assertEqual(
                    result.steps["firmware_state"].status, "skipped"
                )
                self.assertEqual(calls, [["mokutil", "--sb-state"]])

    def test_prepare_fails_closed_when_firmware_state_is_unknown(self):
        calls = []

        def run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 1, "", "probe failed")

        result = operations.prepare(run)
        self.assertFalse(result.ok)
        self.assertEqual(result.steps["firmware_state"].status, "failed")
        self.assertEqual(calls, [["mokutil", "--sb-state"]])

    def test_prepare_uses_fixed_password_without_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            config = root / "dkms" / "anduinos-sb-sign.conf"
            private.write_text("private")
            certificate.write_text("certificate")
            calls = []

            def run(command, **kwargs):
                calls.append((list(command), kwargs))
                code = 1 if command[:2] == ["mokutil", "--test-key"] else 0
                return subprocess.CompletedProcess(command, code, "", "")

            result = operations.prepare(
                with_secure_boot_enabled(run), private, certificate, config
            )
            self.assertTrue(result.ok)
            import_call = next(item for item in calls if item[0][:2] == ["mokutil", "--import"])
            self.assertEqual(import_call[1]["stdin"], "123456\n123456\n")
            self.assertEqual(config.read_text(), operations.CONFIG_CONTENT)
            self.assertTrue(any(command == ["dkms", "autoinstall"] for command, _ in calls))

    def test_dkms_failure_preserves_successful_enrollment_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            config = root / "dkms.conf"
            private.write_text("private")
            certificate.write_text("certificate")

            def run(command, **kwargs):
                code = 1 if command[:2] in (["mokutil", "--test-key"], ["dkms", "autoinstall"]) else 0
                return subprocess.CompletedProcess(command, code, "", "dkms failed" if code else "")

            result = operations.prepare(
                with_secure_boot_enabled(run), private, certificate, config
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.steps["enrollment_queued"].status, "success")
            self.assertEqual(result.steps["modules_rebuilt"].status, "failed")
            self.assertTrue(result.reboot_required)

    def test_pending_enrollment_is_not_queued_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            private.write_text("private")
            certificate.write_text("certificate")
            calls = []

            def run(command, **kwargs):
                calls.append(list(command))
                if command[:2] == ["mokutil", "--list-enrolled"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:2] == ["mokutil", "--list-new"]:
                    return subprocess.CompletedProcess(
                        command, 0, "SHA1 Fingerprint: aa:12\n", ""
                    )
                if command[-2:] == ["-fingerprint", "-sha1"]:
                    return subprocess.CompletedProcess(
                        command, 0, "sha1 Fingerprint=AA:12\n", ""
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            result = operations.prepare(
                with_secure_boot_enabled(run),
                private,
                certificate,
                root / "dkms.conf",
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.steps["enrollment_queued"].status, "skipped")
            self.assertNotIn(["mokutil", "--import", str(certificate)], calls)
            self.assertTrue(result.reboot_required)

    def test_unrelated_pending_certificate_does_not_block_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            private.write_text("private")
            certificate.write_text("certificate")
            calls = []

            def run(command, **kwargs):
                calls.append(list(command))
                if command[:2] == ["mokutil", "--list-enrolled"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[:2] == ["mokutil", "--list-new"]:
                    return subprocess.CompletedProcess(
                        command, 0, "SHA1 Fingerprint: bb:34\n", ""
                    )
                if command[-2:] == ["-fingerprint", "-sha1"]:
                    return subprocess.CompletedProcess(
                        command, 0, "sha1 Fingerprint=AA:12\n", ""
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            result = operations.prepare(
                with_secure_boot_enabled(run),
                private,
                certificate,
                root / "dkms.conf",
            )
            self.assertTrue(result.ok)
            self.assertIn(["mokutil", "--import", str(certificate)], calls)

    def test_already_enrolled_certificate_is_never_imported_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            private.write_text("private")
            certificate.write_text("certificate")
            calls = []

            def run(command, **kwargs):
                calls.append(list(command))
                if command[:2] == ["mokutil", "--list-enrolled"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[:2] == ["mokutil", "--test-key"]:
                    return subprocess.CompletedProcess(
                        command, 1, f"{certificate} is already enrolled\n", ""
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            result = operations.prepare(
                with_secure_boot_enabled(run),
                private,
                certificate,
                root / "dkms.conf",
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.steps["enrollment_queued"].status, "skipped")
            self.assertNotIn(["mokutil", "--import", str(certificate)], calls)
            self.assertFalse(result.reboot_required)

    def test_repair_rewrites_configuration_before_rebuilding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "MOK.priv"
            certificate = root / "MOK.der"
            configuration = root / "dkms.conf"
            private.write_text("private")
            certificate.write_text("certificate")
            calls = []

            def run(command, **kwargs):
                calls.append(list(command))
                return subprocess.CompletedProcess(command, 0, "", "")

            result = operations.repair_dkms(
                with_secure_boot_enabled(run),
                private,
                certificate,
                configuration,
            )
            self.assertTrue(result.ok)
            self.assertEqual(configuration.read_text(), operations.CONFIG_CONTENT)
            self.assertEqual(calls, [["dkms", "autoinstall", "--force"]])


if __name__ == "__main__":
    unittest.main()
