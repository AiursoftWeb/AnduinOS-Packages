"""Source lifecycle tests; require bubblewrap with working user namespaces."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "etc/pam.d").mkdir(parents=True)
        (self.root / "etc/sudoers.d").mkdir()
        (self.root / "var/lib/anduinos-passwordless-sudo").mkdir(parents=True)
        self.base_pam = (
            "@include common-auth\n"
            "# Administrator-managed U2F rule\n"
            "auth sufficient pam_u2f.so authfile=/etc/admin-keys\n"
        )
        self.managed_pam = (
            "# Managed by anduinos-yubikey-manager\n"
            "auth sufficient pam_u2f.so authfile=/etc/anduinos-keys\n"
        )
        for service in ("gdm-password", "sudo"):
            (self.root / "etc/pam.d" / service).write_text(self.managed_pam + self.base_pam)
        self.shared = self.root / "etc/sudoers.d/90-anduinos-passwordless-admin"
        self.shared.write_text("alice ALL=(ALL:ALL) NOPASSWD: ALL\n")
        self.legacy = self.root / "etc/sudoers.d/90-anduinos-yubikey-manager"
        self.legacy.write_text("legacy policy\n")
        self.state = self.root / "var/lib/anduinos-passwordless-sudo/users"
        self.state.write_text("alice\n")

    def run_script(self, name, action, *, helper=None):
        # The real script runs with an isolated /etc and /var. The host root is
        # read-only and there is no host network, session bus or device access.
        command = [
            "bwrap", "--unshare-all", "--die-with-parent",
            "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp", "--bind", str(self.root / "etc"), "/etc",
            "--bind", str(self.root / "var"), "/var",
        ]
        source = (ROOT / "scripts" / name).read_text()
        if helper is not None:
            command += ["--ro-bind", str(helper), "/tmp/test-helper"]
            source = source.replace("/usr/lib/anduinos-yubikey-manager/helper", "/tmp/test-helper")
        return subprocess.run(
            [*command, "/bin/sh", "-s", "--", action],
            input=source, capture_output=True, text=True, timeout=15,
        )

    def test_upgrade_preserves_authentication_configuration(self):
        for action in ("upgrade", "failed-upgrade", "abort-upgrade"):
            with self.subTest(action=action):
                result = self.run_script("prerm.sh", action)
                self.assertEqual(result.returncode, 0, result.stderr)
                for service in ("gdm-password", "sudo"):
                    self.assertEqual(
                        (self.root / "etc/pam.d" / service).read_text(),
                        self.managed_pam + self.base_pam,
                    )
                self.assertEqual(self.shared.read_text(), "alice ALL=(ALL:ALL) NOPASSWD: ALL\n")
                self.assertEqual(self.state.read_text(), "alice\n")
                self.assertEqual(self.legacy.read_text(), "legacy policy\n")

    def test_removal_detaches_only_owned_pam_rules_and_preserves_shared_state(self):
        result = self.run_script("prerm.sh", "remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        for service in ("gdm-password", "sudo"):
            self.assertEqual((self.root / "etc/pam.d" / service).read_text(), self.base_pam)
        self.assertFalse(self.legacy.exists())
        self.assertEqual(self.shared.read_text(), "alice ALL=(ALL:ALL) NOPASSWD: ALL\n")
        self.assertEqual(self.state.read_text(), "alice\n")
        # Retrying removal must not eat an administrator's neighboring rule.
        result = self.run_script("prerm.sh", "remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "etc/pam.d/sudo").read_text(), self.base_pam)

    def test_removal_does_not_follow_legacy_sudoers_symlink(self):
        self.legacy.unlink()
        self.legacy.symlink_to(self.shared.name)
        result = self.run_script("prerm.sh", "remove")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.legacy.is_symlink())
        self.assertEqual(self.shared.read_text(), "alice ALL=(ALL:ALL) NOPASSWD: ALL\n")

    def test_configuration_reconciles_state_and_reports_repair_failure(self):
        helper = self.root / "test-helper"
        helper.write_text('#!/bin/sh\nprintf "%s\\n" "$*"\nexit 19\n')
        helper.chmod(0o755)
        result = self.run_script("postinst.sh", "configure", helper=helper)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "repair")
        self.assertIn("could not reconcile", result.stderr)
        result = self.run_script("postinst.sh", "abort-upgrade", helper=helper)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
