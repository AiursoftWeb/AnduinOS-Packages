import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


def load_helper():
    loader = importlib.machinery.SourceFileLoader("yubikey_helper", "data/helper")
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class HelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.helper = load_helper()
        self.helper.SUDOERS_DIR = str(self.root / "sudoers.d")
        self.helper.MANAGED_SUDOERS = str(
            self.root / "sudoers.d" / "90-anduinos-yubikey-manager"
        )
        self.helper.METADATA = str(self.root / "enrollments.json")
        Path(self.helper.SUDOERS_DIR).mkdir()
        self.helper.validate_sudoers = lambda: None
        self.helper.validate_user = lambda _username: None
        self.helper.has_usable_password = lambda _username: True

    def tearDown(self):
        self.temp.cleanup()

    def test_disable_removes_only_users_unconditional_rules(self):
        legacy = Path(self.helper.SUDOERS_DIR) / "legacy"
        legacy.write_text(
            "alice ALL=(ALL) NOPASSWD:ALL\n"
            "alice ALL=(ALL:ALL) NOPASSWD: /usr/bin/apt\n"
            "bob ALL=(ALL:ALL) NOPASSWD: ALL\n",
            encoding="utf-8",
        )
        metadata = {"enrollments": [], "passwordless_sudo_users": ["alice"]}

        self.helper.set_passwordless_sudo("alice", False, metadata)

        self.assertEqual(
            legacy.read_text(encoding="utf-8"),
            "alice ALL=(ALL:ALL) NOPASSWD: /usr/bin/apt\n"
            "bob ALL=(ALL:ALL) NOPASSWD: ALL\n",
        )
        self.assertEqual(metadata["passwordless_sudo_users"], [])

    def test_enable_writes_a_visudo_compatible_dropin_shape(self):
        metadata = {"enrollments": []}
        self.helper.set_passwordless_sudo("alice", True, metadata)
        managed = Path(self.helper.MANAGED_SUDOERS)
        self.assertEqual(
            managed.read_text(encoding="utf-8"),
            "alice ALL=(ALL:ALL) NOPASSWD: ALL\n",
        )
        self.assertEqual(managed.stat().st_mode & 0o777, 0o440)
        self.assertEqual(metadata["passwordless_sudo_users"], ["alice"])

    def test_enable_preserves_another_managed_user(self):
        managed = Path(self.helper.MANAGED_SUDOERS)
        managed.write_text(
            "bob ALL=(ALL:ALL) NOPASSWD: ALL\n",
            encoding="utf-8",
        )
        metadata = {"enrollments": [], "passwordless_sudo_users": ["bob"]}

        self.helper.set_passwordless_sudo("alice", True, metadata)

        self.assertEqual(
            managed.read_text(encoding="utf-8"),
            "alice ALL=(ALL:ALL) NOPASSWD: ALL\n"
            "bob ALL=(ALL:ALL) NOPASSWD: ALL\n",
        )
        self.assertEqual(
            metadata["passwordless_sudo_users"],
            ["alice", "bob"],
        )

    def test_repair_rebuilds_mappings_and_pam_from_metadata(self):
        gdm_pam = self.root / "gdm-password"
        sudo_pam = self.root / "sudo"
        gdm_mapping = self.root / "gdm-mappings"
        sudo_mapping = self.root / "sudo-mappings"
        gdm_pam.write_text("@include common-auth\n", encoding="utf-8")
        sudo_pam.write_text("@include common-auth\n", encoding="utf-8")
        self.helper.PAM_CONFIG = {
            "gdm": (str(gdm_pam), str(gdm_mapping)),
            "sudo": (str(sudo_pam), str(sudo_mapping)),
        }
        self.helper.validate_common = lambda _username, _serial: None
        credential = "A" * 40 + ",B"
        metadata = {
            "enrollments": [
                {
                    "username": "alice",
                    "serial": "1234",
                    "purpose": "gdm",
                    "credential": credential,
                },
                {
                    "username": "alice",
                    "serial": "1234",
                    "purpose": "sudo",
                    "credential": credential,
                },
            ]
        }

        self.helper.repair_integrations(metadata)

        self.assertEqual(gdm_mapping.read_text(), f"alice:{credential}\n")
        self.assertEqual(sudo_mapping.read_text(), f"alice:{credential}\n")
        self.assertIn(
            "# Managed by anduinos-yubikey-manager (gdm)",
            gdm_pam.read_text(),
        )
        self.assertIn(
            "# Managed by anduinos-yubikey-manager (sudo)",
            sudo_pam.read_text(),
        )

    @mock.patch("subprocess.run")
    def test_install_git_uses_a_fixed_noninteractive_apt_command(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="")

        self.helper.install_git()

        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/apt-get", "install", "-y", "git"])
        self.assertEqual(kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
        self.assertEqual(kwargs["stderr"], self.helper.subprocess.STDOUT)

    @mock.patch("subprocess.run")
    def test_install_git_reports_apt_output_on_failure(self, run):
        run.return_value = mock.Mock(returncode=100, stdout="apt-get details")
        self.helper.fail = mock.Mock(side_effect=RuntimeError)

        with self.assertRaises(RuntimeError):
            self.helper.install_git()

        self.helper.fail.assert_called_once_with("apt-get details")


if __name__ == "__main__":
    unittest.main()
