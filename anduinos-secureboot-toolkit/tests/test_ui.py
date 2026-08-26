from pathlib import Path
import subprocess
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_secureboot.ui import restart_to_firmware_settings  # noqa: E402


class FirmwareSettingsTests(unittest.TestCase):
    def test_restart_uses_the_fixed_systemd_firmware_command(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        self.assertEqual(restart_to_firmware_settings(runner), (True, ""))
        self.assertEqual(
            calls[0][0],
            ["systemctl", "reboot", "--firmware-setup"],
        )
        self.assertFalse(calls[0][1]["check"])
        self.assertEqual(calls[0][1]["timeout"], 10)

    def test_restart_returns_firmware_errors_to_the_ui(self):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 1, "", "not supported")

        self.assertEqual(
            restart_to_firmware_settings(runner),
            (False, "not supported"),
        )


if __name__ == "__main__":
    unittest.main()
