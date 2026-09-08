import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]

class PackageContractTests(unittest.TestCase):

    def test_internal_vm_clis_load_but_have_no_public_launcher(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT / "src")
        for name in (
            "guided_test_plan_cli.py",
            "guided_test_evidence_cli.py",
        ):
            with self.subTest(name=name):
                result = subprocess.run(
                    (sys.executable, str(ROOT / "src" / name), "--help"),
                    capture_output=True,
                    text=True,
                    env=environment,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        launcher = ROOT / "assets/anduinos-installer-executor"
        result = subprocess.run(
            ("/bin/sh", str(launcher), "--guided-destructive-test"),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not accept arguments", result.stderr)

if __name__ == "__main__":
    unittest.main()
