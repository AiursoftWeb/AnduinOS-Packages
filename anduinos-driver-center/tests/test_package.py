from pathlib import Path
import re
import subprocess
import tempfile
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
        self.assertIn('case ["repair-audio"]:', helper)
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
        self.assertIn('"resume-print-queues"', application)
        self.assertIn('Gtk.Button(label=_("Add Printer"))', application)
        self.assertIn('["gnome-control-center", "printers"]', application)
        self.assertIn("if state.service_running:\n            printer_actions", application)

    def test_warning_rows_require_a_recovery_action(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        self.assertIn("Warning row has no recovery action", application)
        self.assertIn("Warning banner has no recovery action", application)
        self.assertNotIn("blocked_by_secure_boot", application)
        self.assertNotIn("repair.set_sensitive(secure_boot.ready)", application)
        self.assertEqual(application.count("Adw.Banner("), 1)

    def test_xpadneo_package_scripts_propagate_real_dkms_failures(self):
        package = ROOT.parent / "anduinos-xbox-controller-driver"
        postinst = (package / "scripts/postinst.sh").read_text()
        prerm = (package / "scripts/prerm.sh").read_text()
        self.assertNotIn("|| true", postinst)
        self.assertNotIn("|| true", prerm)
        self.assertIn('dkms build -m "$PKG_NAME"', postinst)
        self.assertIn('dkms install -m "$PKG_NAME"', postinst)
        self.assertIn('dkms status -m "$PKG_NAME" -v "$VERSION"', postinst)
        self.assertIn('-k "$KERNEL_RELEASE"', postinst)
        self.assertIn('*": installed"*)', postinst)
        self.assertIn('dkms status -m "$PKG_NAME" -v "$VERSION"', prerm)
        self.assertNotIn('/var/lib/dkms/', prerm)

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
        self.assertIn("DBusActivatable=true", desktop)
        self.assertNotIn("NoDisplay=true", desktop)

    def test_driver_illustrations_are_parseable_local_svg_files(self):
        expected = {
            "audio.svg",
            "firmware.svg",
            "nvidia.svg",
            "input-gaming.svg",
            "printer.svg",
            "secureboot-chip.svg",
        }
        actual = {path.name for path in (ROOT / "resources").glob("*.svg")}
        self.assertEqual(actual, expected)
        for path in (ROOT / "resources").glob("*.svg"):
            root = ET.parse(path).getroot()
            self.assertTrue(root.tag.endswith("svg"))

    def test_home_page_reuses_the_desktop_application_icon(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        self.assertIn("Gtk.Image.new_from_icon_name(APP_ID)", application)
        self.assertNotIn('driver-overview.svg', application)

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

    def test_home_page_uses_the_restricted_recommended_install_action(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        helper = (ROOT / "scripts/driver-helper").read_text()
        self.assertIn('home_row.page_name = "home"', application)
        self.assertIn('["install-recommended"]', application)
        self.assertIn('case ["install-recommended"]:', helper)
        self.assertIn('["refresh-driver-info"]', application)
        self.assertIn('case ["refresh-driver-info"]:', helper)
        self.assertIn('title="ubuntu-drivers install"', application)
        self.assertIn(
            'success_output_marker="+ ubuntu-drivers install"', application
        )

    def test_firmware_management_uses_fwupd_below_secure_boot(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        firmware = (ROOT / "src/anduinos_driver_center/firmware.py").read_text()
        rust_firmware = (ROOT / "src/firmware.rs").read_text()
        rust_window = (ROOT / "src/window.rs").read_text()
        project = (ROOT / "anduinos-driver-center.aosproj").read_text()
        secure_boot = application.index('secure_row.page_name = "secure-boot"')
        firmware_row = application.index('firmware_row.page_name = "firmware"')
        self.assertLess(secure_boot, firmware_row)
        self.assertIn('<Dependency Include="fwupd" />', project)
        self.assertNotIn("gir1.2-fwupd-2.0", project)
        self.assertIn("fwupdmgr", rust_firmware)
        self.assertIn("get-devices", rust_firmware)
        self.assertIn("get-updates", rust_firmware)
        self.assertIn("get-history", rust_firmware)
        self.assertIn("get_devices_async", firmware)
        self.assertIn("get_upgrades_async", firmware)
        self.assertIn("refresh_remote_async", firmware)
        self.assertIn("install_release_async", firmware)
        self.assertIn("get_history_async", firmware)
        self.assertIn("set_feature_flags", firmware)
        self.assertIn('connect("device-changed"', firmware)
        self.assertIn("DEVICE_FLAG_AFFECTS_FDE", firmware)
        self.assertIn("device-request", firmware)
        self.assertIn("Update All Firmware", rust_window)
        self.assertIn("Firmware Update History", rust_window)
        self.assertIn("Restart Now", rust_window)
        self.assertIn('_("Update All Firmware")', application)
        self.assertIn('_("Firmware Update History")', application)
        self.assertIn('_("Restart Now")', application)
        self.assertIn("device.affects_fde", application)
        self.assertIn("device.release.description", application)
        self.assertIn("wait_check_async", application)
        self.assertNotIn('case ["install-firmware"', (ROOT / "scripts/driver-helper").read_text())

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
        self.assertIn("clamp.set_child(secure_boot_page)", application)
        self.assertIn("Adw.Clamp(maximum_size=650, tightening_threshold=500)", application)
        self.assertIn("secure_boot_page.set_valign(Gtk.Align.START)", application)
        self.assertIn("secure_boot_page.set_vexpand(False)", application)
        self.assertIn("_inspect_secure_boot", core)
        self.assertNotIn('case ["repair-dkms"]', helper)
        self.assertNotIn('case ["enroll-mok"]', helper)

    def test_secure_boot_navigation_includes_indeterminate_state(self):
        application = (ROOT / "src/anduinos_driver_center/app.py").read_text()
        guard = application.index("if not secure_boot.enforcement_inactive:")
        navigation = application.index(
            'secure_row.page_name = "secure-boot"', guard
        )
        next_method = application.index("\n    def _device_row", guard)
        self.assertLess(guard, navigation)
        self.assertLess(navigation, next_method)

    def test_every_supported_locale_is_complete_and_matches_the_ui(self):
        expected_locales = {
            "ar", "da", "de", "el", "en_GB", "en_US", "es", "fi", "fr",
            "hi", "id", "it", "ja", "ko", "nl", "pl", "pt", "pt_BR",
            "ro", "ru", "sv", "th", "tr", "uk", "vi", "zh_CN", "zh_HK",
            "zh_TW",
        }
        po_files = sorted((ROOT / "po").glob("*.po"))
        self.assertEqual({path.stem for path in po_files}, expected_locales)

        toolkit_ui = ROOT.parent / "anduinos-secureboot-toolkit" / "src" / "anduinos_secureboot" / "ui.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            extracted = Path(temporary_directory) / "messages.pot"
            subprocess.run(
                [
                    "xgettext", "--language=Python", "--keyword=_",
                    "--keyword=ngettext:1,2", "--from-code=UTF-8",
                    f"--output={extracted}",
                    str(ROOT / "src" / "anduinos_driver_center" / "app.py"),
                    str(toolkit_ui),
                ],
                check=True,
            )
            template_difference = subprocess.run(
                [
                    "msgcomm", "--less-than=2", "--omit-header",
                    str(ROOT / "po" / "anduinos-driver-center.pot"), str(extracted),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(template_difference.stdout.strip(), "")

        translated_msgid = re.compile(r'^msgid "[^"].*"$', re.MULTILINE)
        for po_file in po_files:
            subprocess.run(
                ["msgfmt", "--check", "--check-format", "--output-file=/dev/null", str(po_file)],
                check=True,
            )
            for selector in ("--untranslated", "--only-fuzzy"):
                result = subprocess.run(
                    ["msgattrib", selector, "--no-obsolete", str(po_file)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertIsNone(
                    translated_msgid.search(result.stdout),
                    f"{po_file.name} contains {selector.removeprefix('--')} messages",
                )


if __name__ == "__main__":
    unittest.main()
