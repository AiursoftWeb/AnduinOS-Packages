from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from anduinos_driver_center.app import _command_output_summary  # noqa: E402


class AppTests(unittest.TestCase):
    def test_recommended_install_uses_the_ubuntu_drivers_conclusion(self):
        output = """+ apt-get update
Reading package lists... Done
+ ubuntu-drivers install
All the available drivers are already installed.
Driver operation completed successfully.
"""
        self.assertEqual(
            _command_output_summary(output, "+ ubuntu-drivers install"),
            "All the available drivers are already installed.",
        )

    def test_command_summary_requires_the_requested_command_marker(self):
        self.assertIsNone(
            _command_output_summary(
                "Driver operation completed successfully.",
                "+ ubuntu-drivers install",
            )
        )


if __name__ == "__main__":
    unittest.main()
