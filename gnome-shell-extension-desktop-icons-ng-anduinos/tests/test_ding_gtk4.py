import json
import os
from pathlib import Path
import stat
import tempfile
import subprocess
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
UUID = "ding@rastersoft.com"


class DingGtk4PackageTests(unittest.TestCase):
    def test_suites_downloaded_with_expected_versions_and_permissions(self):
        project = ET.parse(ROOT / "gnome-shell-extension-desktop-icons-ng-anduinos.aosproj")
        suites = project.findtext(".//TargetSuites").split()
        expected = {"noble": 85, "resolute": 105, "stonking": 105}
        for suite_name in suites:
            suite = suite_name.removesuffix("-addon")
            ext_dir = ROOT / "deploy" / suite / UUID
            self.assertTrue(ext_dir.is_dir(), f"{suite}: missing {ext_dir}")
            meta = json.loads((ext_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["uuid"], UUID)
            self.assertEqual(meta["version"], expected[suite])
            self.assertTrue((ext_dir / "schemas/gschemas.compiled").is_file())
            ding_mode = stat.S_IMODE((ext_dir / "app/ding.js").stat().st_mode)
            self.assertEqual(ding_mode, 0o755)
            meta_mode = stat.S_IMODE((ext_dir / "metadata.json").stat().st_mode)
            self.assertEqual(meta_mode, 0o644)

    def test_gtk4_javascript_module_syntax(self):
        for suite in ("resolute", "stonking"):
            ext_dir = ROOT / "deploy" / suite / UUID
            js_files = sorted(ext_dir.rglob("*.js"))
            self.assertTrue(js_files)
            for js_file in js_files:
                with self.subTest(suite=suite, file=str(js_file.relative_to(ext_dir))):
                    res = subprocess.run(
                        ["node", "--input-type=module", "--check"],
                        input=js_file.read_text(encoding="utf-8"),
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(res.returncode, 0, res.stderr)

    def test_gtk4_root_cause_invariants(self):
        for suite in ("resolute", "stonking"):
            ext_dir = ROOT / "deploy" / suite / UUID
            ext_js = (ext_dir / "extension.js").read_text(encoding="utf-8")
            dbus_js = (ext_dir / "app/dbusUtils.js").read_text(encoding="utf-8")
            dm_js = (ext_dir / "app/desktopManager.js").read_text(encoding="utf-8")
            dg_js = (ext_dir / "app/desktopGrid.js").read_text(encoding="utf-8")
            em_js = (ext_dir / "emulateX11WindowType.js").read_text(encoding="utf-8")

            self.assertNotIn("force_exit()", ext_js)
            self.assertIn("is_stage_views_scaled", ext_js)
            self.assertNotIn("getIntrospectionData(\n        'org.gnome.Nautilus'", dbus_js)
            self.assertIn("DO_NOT_AUTO_START_AT_CONSTRUCTION", dbus_js)
            self.assertNotIn("['nautilus', '--version']", dm_js)
            self.assertIn("find_program_in_path('nautilus')", dm_js)
            self.assertIn("resizeWindow(", dm_js)
            self.assertIn("resizeWindow(desktopDescription)", dg_js)
            self.assertIn("Clutter.Clone", em_js)
            self.assertIn("_updateTargetCoordinates", em_js)

    def test_gtk4_window_manager_behavioral_harness(self):
        for suite in ("resolute", "stonking"):
            ext_dir = ROOT / "deploy" / suite / UUID
            with self.subTest(suite=suite), tempfile.TemporaryDirectory() as tmp_dir:
                res = subprocess.run(
                    ["gjs", "-m", str(ROOT / "tests/ding-gtk4-behavior.js"), str(ext_dir), tmp_dir],
                    env={**os.environ, "GSETTINGS_BACKEND": "memory"},
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
                self.assertIn("6 GTK4 DING behavioral cases passed", res.stdout)


if __name__ == "__main__":
    unittest.main()
