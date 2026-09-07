import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

script = Path(__file__).resolve().parents[1] / "scripts/smoke-shell.py"
spec = importlib.util.spec_from_file_location("shell_smoke", script)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class DesktopIsolationTests(unittest.TestCase):
    def test_child_refuses_ordinary_desktop_environment(self):
        with tempfile.TemporaryDirectory(prefix="anduinos-shell-e2e.") as name:
            with patch.dict(os.environ, {"ANDUINOS_DESKTOP_SMOKE": "0"}):
                with self.assertRaises(AssertionError):
                    smoke.guard(Path(name))

    def test_child_refuses_real_runtime_directory(self):
        with tempfile.TemporaryDirectory(prefix="anduinos-shell-e2e.") as name:
            with patch.dict(os.environ, {"ANDUINOS_DESKTOP_SMOKE": "1",
                                        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}):
                with self.assertRaises(AssertionError):
                    smoke.guard(Path(name))

    def test_child_accepts_only_its_owned_test_runtime(self):
        with tempfile.TemporaryDirectory(prefix="anduinos-shell-e2e.") as name:
            with patch.dict(os.environ, {"ANDUINOS_DESKTOP_SMOKE": "1",
                                        "XDG_RUNTIME_DIR": str(Path(name) / "runtime")}):
                smoke.guard(Path(name))
