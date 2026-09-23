"""Build-time notice generation must support rustup and fail closed."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rust-notices.py"
spec = importlib.util.spec_from_file_location("rust_notices", SCRIPT)
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class RustNoticesTests(unittest.TestCase):
    def test_rustup_layouts_and_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            doc = root / "share/doc/rust"
            doc.mkdir(parents=True)
            output = root / "result"
            names = ("COPYRIGHT-library.html", "COPYRIGHT", "COPYRIGHT.html")
            for name in names:
                (doc / name).write_text("notice: " + name)
            for name in names:
                with self.subTest(name=name), patch.dict(os.environ, {"RUST_STDLIB_NOTICES": ""}), \
                        patch.object(notices.sys, "argv", [str(SCRIPT), "x86_64-unknown-linux-gnu", str(output)]), \
                        patch.object(notices.subprocess, "check_output", side_effect=[
                            json.dumps({"packages": [], "resolve": {"nodes": []}}),
                            "rustc 1.98.1", str(root)]):
                    notices.main()
                    self.assertIn("notice: " + name, output.read_text())
                    self.assertEqual(output.read_text().count("notice: "), 1)
                (doc / name).unlink()

    def test_explicit_notice_override_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            override = root / "custom-notice"
            output = root / "result"
            for exists in (True, False):
                if exists:
                    override.write_text("custom toolchain notice")
                else:
                    override.unlink()
                    output.unlink()
                with self.subTest(exists=exists), patch.dict(os.environ, {"RUST_STDLIB_NOTICES": str(override)}), \
                        patch.object(notices.sys, "argv", [str(SCRIPT), "aarch64-unknown-linux-gnu", str(output)]), \
                        patch.object(notices.subprocess, "check_output", side_effect=[
                            json.dumps({"packages": [], "resolve": {"nodes": []}}),
                            "rustc 1.98.1", str(root)]):
                    if exists:
                        notices.main()
                        self.assertIn("custom toolchain notice", output.read_text())
                    else:
                        with self.assertRaisesRegex(SystemExit, "Missing Rust standard-library notices"):
                            notices.main()
                        self.assertFalse(output.exists())
