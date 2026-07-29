import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest


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
        metadata = {"enrollments": []}

        self.helper.set_passwordless_sudo("alice", False, metadata)

        self.assertEqual(
            legacy.read_text(encoding="utf-8"),
            "alice ALL=(ALL:ALL) NOPASSWD: /usr/bin/apt\n"
            "bob ALL=(ALL:ALL) NOPASSWD: ALL\n",
        )

    def test_enable_writes_a_visudo_compatible_dropin_shape(self):
        self.helper.set_passwordless_sudo("alice", True, {"enrollments": []})
        managed = Path(self.helper.MANAGED_SUDOERS)
        self.assertEqual(
            managed.read_text(encoding="utf-8"),
            "alice ALL=(ALL:ALL) NOPASSWD: ALL\n",
        )
        self.assertEqual(managed.stat().st_mode & 0o777, 0o440)


if __name__ == "__main__":
    unittest.main()
