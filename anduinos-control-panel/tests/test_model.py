from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anduinos_control_panel.model import (  # noqa: E402
    flatpak_installed,
    package_installed,
)


class ProbeTests(unittest.TestCase):
    @staticmethod
    def runner_for(return_codes, output=""):
        calls = []

        def run(arguments, **_kwargs):
            calls.append(arguments)
            code = return_codes.pop(0)
            return subprocess.CompletedProcess(arguments, code, stdout=output)

        return run, calls

    def test_package_probe_requires_fully_installed_status(self):
        installed, calls = self.runner_for([0], "ii ")
        self.assertTrue(package_installed("flatseal", installed))
        self.assertEqual(calls[0][-1], "flatseal")

        removed, _calls = self.runner_for([0], "rc ")
        self.assertFalse(package_installed("flatseal", removed))

    def test_flatpak_probe_checks_user_then_system_scope(self):
        system_install, calls = self.runner_for([1, 0])
        self.assertTrue(flatpak_installed("org.example.App", system_install))
        self.assertEqual(calls[0][1], "--user")
        self.assertEqual(calls[1][1], "--system")

if __name__ == "__main__":
    unittest.main()
