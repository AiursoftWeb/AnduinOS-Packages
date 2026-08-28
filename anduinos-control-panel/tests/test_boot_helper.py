import importlib.machinery
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader(
    "boot_settings_helper", str(ROOT / "scripts/boot-settings-helper")
)
boot_settings_helper = types.ModuleType(loader.name)
loader.exec_module(boot_settings_helper)


class BootSettingsHelperTests(unittest.TestCase):
    def test_timeout_is_restricted_to_a_small_numeric_range(self):
        self.assertEqual(boot_settings_helper.parse_timeout("10"), 10)
        for value in ("-1", "3.5", "ten", "301", "١٠"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                boot_settings_helper.parse_timeout(value)

    def test_setting_timeout_updates_normal_and_interrupted_boot_delays(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = Path(directory) / "grub.d" / "99-test.cfg"
            with (
                patch.object(boot_settings_helper, "CONFIGURATION_PATH", configuration),
                patch.object(boot_settings_helper, "update_grub") as update_grub,
            ):
                boot_settings_helper.set_timeout(10)

            self.assertEqual(
                configuration.read_text(),
                "# Managed by AnduinOS Control Panel.\n"
                "GRUB_TIMEOUT=10\n"
                "GRUB_RECORDFAIL_TIMEOUT=10\n",
            )
            self.assertEqual(
                stat.S_IMODE(configuration.stat().st_mode), 0o644
            )
            update_grub.assert_called_once_with()

    def test_failed_grub_refresh_restores_the_previous_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = Path(directory) / "99-test.cfg"
            configuration.write_text("previous\n")
            with (
                patch.object(boot_settings_helper, "CONFIGURATION_PATH", configuration),
                patch.object(
                    boot_settings_helper,
                    "update_grub",
                    side_effect=RuntimeError("failed"),
                ),
                self.assertRaises(RuntimeError),
            ):
                boot_settings_helper.set_timeout(10)

            self.assertEqual(configuration.read_text(), "previous\n")

    def test_main_requires_root_and_a_complete_fixed_action(self):
        with (
            patch.object(boot_settings_helper.os, "geteuid", return_value=1000),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                boot_settings_helper.main(["set-timeout", "10"]), 77
            )
        with (
            patch.object(boot_settings_helper.os, "geteuid", return_value=0),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(boot_settings_helper.main(["other", "10"]), 64)

    def test_main_passes_only_a_validated_integer_to_the_operation(self):
        with (
            patch.object(boot_settings_helper.os, "geteuid", return_value=0),
            patch.object(boot_settings_helper, "set_timeout") as set_timeout,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                boot_settings_helper.main(["set-timeout", "10"]), 0
            )
        set_timeout.assert_called_once_with(10)


if __name__ == "__main__":
    unittest.main()
