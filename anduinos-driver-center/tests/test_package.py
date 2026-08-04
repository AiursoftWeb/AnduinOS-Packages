from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_all_python_sources_compile_without_creating_cache_files(self):
        sources = [
            *ROOT.glob("scripts/*"),
            *ROOT.glob("src/anduinos_driver_center/*.py"),
        ]
        for source in sources:
            if not source.is_file():
                continue
            compile(source.read_text(), str(source), "exec")

    def test_python_package_uses_importable_underscore_name(self):
        self.assertTrue((ROOT / "src/anduinos_driver_center/__init__.py").is_file())
        self.assertFalse((ROOT / "src/anduinos-driver_center").exists())

    def test_application_exposes_standard_about_menu(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        self.assertIn('menu.append(_("About Driver Center"), "app.about")', application)
        self.assertIn('Gio.SimpleAction.new("about", None)', application)
        self.assertIn("Adw.AboutDialog()", application)

    def test_audio_install_action_uses_the_restricted_helper(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        helper = (ROOT / "scripts/driver-helper").read_text()
        self.assertIn('["install-audio"]', application)
        self.assertIn('case ["install-audio"]:', helper)
        self.assertIn(
            'AUDIO_PACKAGES = ("firmware-sof-anduinos", "alsa-ucm-conf-anduinos")',
            helper,
        )

    def test_printing_page_exposes_status_and_package_groups(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        core = (ROOT / "src/anduinos_driver_center/core.py").read_text()
        self.assertIn('printing_row.page_name = "printing"', application)
        self.assertIn('_("Core printing")', application)
        self.assertIn('_("Driverless printing")', application)
        self.assertIn('_("Network discovery")', application)
        self.assertIn('"printer-driver-all"', core)
        self.assertIn('"sane-airscan"', core)
        self.assertIn('["install-printing-support"]', application)
        self.assertIn('Adw.SwitchRow(', application)
        self.assertIn('"set-printing-enabled"', application)

    def test_refresh_preserves_the_selected_hardware_page(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        self.assertIn("self._selected_page_name = row.page_name", application)
        self.assertIn(
            'getattr(row, "page_name", None) == self._selected_page_name',
            application,
        )
        self.assertIn("if self._rebuilding_navigation:", application)

    def test_desktop_entry_is_visible_and_uses_stable_app_id(self):
        desktop = (ROOT / "data/com.anduinos.DriverCenter.desktop").read_text()
        self.assertIn("Type=Application", desktop)
        self.assertIn("Icon=com.anduinos.DriverCenter", desktop)
        self.assertNotIn("NoDisplay=true", desktop)

    def test_driver_illustrations_are_parseable_local_svg_files(self):
        expected = {"nvidia.svg", "input-gaming.svg", "secureboot-chip.svg"}
        actual = {path.name for path in (ROOT / "resources").glob("*.svg")}
        self.assertEqual(actual, expected)
        for path in (ROOT / "resources").glob("*.svg"):
            root = ET.parse(path).getroot()
            self.assertTrue(root.tag.endswith("svg"))
            self.assertNotIn("data:image", path.read_text())

    def test_polkit_only_authorizes_the_fixed_helper(self):
        tree = ET.parse(ROOT / "data/com.anduinos.DriverCenter.policy")
        annotations = {
            node.attrib.get("key"): (node.text or "").strip()
            for node in tree.findall(".//annotate")
        }
        self.assertEqual(
            annotations["org.freedesktop.policykit.exec.path"],
            "/usr/libexec/anduinos-driver-center/driver-helper",
        )
        self.assertNotIn("org.freedesktop.policykit.exec.allow_gui", annotations)

    def test_helper_does_not_execute_shell_fragments(self):
        helper = (ROOT / "scripts/driver-helper").read_text()
        self.assertNotIn("shell=True", helper)
        self.assertNotIn("bash -c", helper)
        self.assertNotIn("sh -c", helper)

    def test_secure_boot_experience_comes_from_shared_toolkit(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        core = (ROOT / "src/anduinos_driver_center/core.py").read_text()
        helper = (ROOT / "scripts/driver-helper").read_text()
        self.assertIn("create_secure_boot_page", application)
        self.assertIn("_inspect_secure_boot", core)
        self.assertNotIn('case ["repair-dkms"]', helper)
        self.assertNotIn('case ["enroll-mok"]', helper)

    def test_secure_boot_navigation_only_exists_when_firmware_enforces_it(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        guard = application.index("if secure_boot.enabled:")
        navigation = application.index(
            'secure_row.page_name = "secure-boot"', guard
        )
        next_method = application.index("\n    def _device_row", guard)
        self.assertLess(guard, navigation)
        self.assertLess(navigation, next_method)


if __name__ == "__main__":
    unittest.main()
