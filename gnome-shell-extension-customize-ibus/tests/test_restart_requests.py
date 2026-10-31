import os
from pathlib import Path
import subprocess
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
UUID = "customize-ibus@hollowman.ml"


class RestartRequestTests(unittest.TestCase):
    def test_downloaded_extensions_with_real_gsettings_bindings(self):
        project = ET.parse(ROOT / "gnome-shell-extension-customize-ibus.aosproj")
        suites = project.findtext(".//TargetSuites").split()
        for suite in suites:
            self.assertTrue(
                (ROOT / "deploy" / suite.removesuffix("-addon") / UUID).is_dir(),
                f"{suite}: run download.sh before testing",
            )

        # Also exercise dynamically downloaded suites not yet packaged (GNOME 51).
        extensions = sorted((ROOT / "deploy").glob(f"*/{UUID}"))
        self.assertTrue(extensions, "run download.sh before testing")
        for extension in extensions:
            with self.subTest(suite=extension.parent.name):
                result = subprocess.run(
                    ["gjs", str(ROOT / "tests/restart-requests.js"), str(extension)],
                    env={
                        **os.environ,
                        "GSETTINGS_BACKEND": "memory",
                        "GSETTINGS_SCHEMA_DIR": str(extension / "schemas"),
                    },
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("9 restart request cases passed", result.stdout)

    def test_downloaded_javascript_syntax(self):
        extensions = sorted((ROOT / "deploy").glob(f"*/{UUID}"))
        self.assertTrue(extensions, "run download.sh before testing")
        for extension in extensions:
            with self.subTest(suite=extension.parent.name):
                result = subprocess.run(
                    ["node", "--input-type=module", "--check"],
                    input=(extension / "extension.js").read_text(encoding="utf-8"),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
