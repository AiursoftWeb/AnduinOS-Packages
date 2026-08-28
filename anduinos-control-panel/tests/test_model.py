from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anduinos_control_panel.model import (  # noqa: E402
    flatpak_installed,
    package_installed,
    read_grub_timeouts,
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

    def test_grub_timeout_probe_follows_shell_configuration_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = root / "grub"
            drop_ins = root / "grub.d"
            drop_ins.mkdir()
            defaults.write_text(
                "GRUB_TIMEOUT=5\nGRUB_RECORDFAIL_TIMEOUT='30'\n"
            )
            (drop_ins / "10-vendor.cfg").write_text("GRUB_TIMEOUT=3\n")
            (drop_ins / "99-local.cfg").write_text(
                'export GRUB_TIMEOUT="10" # chosen locally\n'
                "GRUB_RECORDFAIL_TIMEOUT=10\n"
            )

            timeouts = read_grub_timeouts(defaults, drop_ins)

        self.assertEqual(timeouts.normal, 10)
        self.assertEqual(timeouts.after_interrupted_boot, 10)

    def test_grub_timeout_probe_does_not_execute_shell_expressions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = root / "grub"
            defaults.write_text(
                "GRUB_TIMEOUT=$(touch /tmp/should-never-exist)\n"
                "GRUB_RECORDFAIL_TIMEOUT=-1\n"
            )

            timeouts = read_grub_timeouts(defaults, root / "missing")

        self.assertEqual(timeouts.normal, 10)
        self.assertEqual(timeouts.after_interrupted_boot, 30)

if __name__ == "__main__":
    unittest.main()
