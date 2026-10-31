"""Source lifecycle tests with all package-owned paths redirected to fixtures."""
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

    def script_source(self, name, *, helper=None):
        source = (ROOT / "scripts" / name).read_text()
        replacements = {
            "/etc/pam.d": self.root / "etc/pam.d",
            "/etc/sudoers.d": self.root / "etc/sudoers.d",
            "/var/lib/anduinos-passwordless-sudo": self.root / "var/lib/anduinos-passwordless-sudo",
        }
        if helper is not None:
            replacements["/usr/lib/anduinos-yubikey-manager/helper"] = helper
        for original, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            source = source.replace(original, str(replacement))
        guarded_source = source
        for replacement in replacements.values():
            guarded_source = guarded_source.replace(str(replacement), "<test-fixture>")
        # Every known package-owned absolute path must disappear outside the
        # explicitly redirected fixture names before the script may execute.
        for original in replacements:
            self.assertNotIn(original, guarded_source)
        for host_prefix in ("/etc/", "/var/", "/run/", "/boot/", "/usr/lib/anduinos-"):
            self.assertNotIn(host_prefix, guarded_source)
        return source

    def run_script(self, name, action, *, helper=None):
        return subprocess.run(
            ["/bin/sh", "-s", "--", action],
            input=self.script_source(name, helper=helper),
            capture_output=True, text=True, timeout=15,
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
