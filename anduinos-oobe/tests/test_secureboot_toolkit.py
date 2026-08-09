from pathlib import Path
from importlib.machinery import SourceFileLoader
import re
import subprocess
import unittest
from unittest import mock
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OOBE = SourceFileLoader(
    "anduinos_oobe_behavior", str(ROOT / "assets/anduinos-oobe")
).load_module()


class SecureBootToolkitTests(unittest.TestCase):
    def test_oobe_embeds_the_shared_secure_boot_page(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        self.assertIn("_shared_secure_boot_page", application)
        self.assertNotIn("dkms autoinstall", application)
        self.assertNotIn("update-secureboot-policy --new-key", application)
        self.assertNotIn("['mokutil'", application)
        self.assertNotIn("['openssl'", application)
        self.assertNotIn("['modinfo'", application)

    def test_hardware_page_follows_optional_secure_boot_page(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        guard = application.index(
            "if not _inspect_secure_boot().enforcement_inactive:"
        )
        secure_boot_page = application.index(
            "factories.append(lambda: create_secureboot_page", guard
        )
        hardware_page = application.index(
            "lambda: create_hardware_drivers_page", secure_boot_page
        )
        self.assertLess(guard, secure_boot_page)
        self.assertLess(secure_boot_page, hardware_page)

    def test_old_hardware_workflows_are_removed_from_oobe(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        for removed in (
            "def create_nvidia_page",
            "def create_xbox_page",
            "def _xbox_recovery_action",
            "XboxStatus as _XboxStatus",
            "graphics_devices as _graphics_devices",
            "xbox_state as _inspect_xbox",
            "DRIVER_CENTER_HELPER",
            "repair-nvidia",
            "install-xbox",
            "repair-xbox",
            "def has_nvidia_gpu",
            "def is_virtual_machine",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, application)

    def test_hardware_page_uses_both_icons_and_custom_navigation(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        hardware = application[
            application.index("def create_hardware_drivers_page"):
            application.index("def create_exe_sandbox_page")
        ]
        self.assertIn("'nvidia.svg'", hardware)
        self.assertIn("'input-gaming.svg'", hardware)
        self.assertIn("page._hide_next = True", hardware)
        self.assertNotIn("page._requires_internet", hardware)
        self.assertIn("_('Open Driver Center')", hardware)
        self.assertIn("_('Skip')", hardware)
        self.assertIn("open_btn.add_css_class('suggested-action')", hardware)

    def test_open_driver_center_launches_without_elevation_then_navigates(self):
        navigate_next = mock.Mock()
        with mock.patch.object(OOBE.subprocess, "Popen") as popen:
            error = OOBE._open_driver_center(navigate_next)

        self.assertIsNone(error)
        popen.assert_called_once_with(["/usr/bin/anduinos-driver-center"])
        navigate_next.assert_called_once_with()

    def test_open_driver_center_failure_stays_on_page_with_localized_error(self):
        navigate_next = mock.Mock()
        with mock.patch.object(
            OOBE.subprocess,
            "Popen",
            side_effect=FileNotFoundError("missing executable"),
        ):
            error = OOBE._open_driver_center(navigate_next)

        self.assertEqual(
            error,
            OOBE._("Could not open AnduinOS Driver Center: {}").format(
                "missing executable"
            ),
        )
        navigate_next.assert_not_called()

    def test_skip_navigates_without_starting_driver_center(self):
        navigate_next = mock.Mock()
        with mock.patch.object(OOBE.subprocess, "Popen") as popen:
            OOBE._skip_hardware_drivers(navigate_next)

        popen.assert_not_called()
        navigate_next.assert_called_once_with()

    def test_oobe_declares_only_shared_hardware_dependencies(self):
        project = ET.parse(ROOT / "anduinos-oobe.aosproj").getroot()
        dependencies = {item.get("Include") for item in project.iter("Dependency")}
        self.assertIn("anduinos-secureboot-toolkit", dependencies)
        self.assertIn("anduinos-driver-center (>= 2.0.0-8)", dependencies)
        self.assertNotIn("ubuntu-drivers-common", dependencies)
        self.assertNotIn("pciutils", dependencies)

    def test_all_oobe_catalogs_are_complete_and_format_safe(self):
        translated_msgid = re.compile(r'^msgid "[^"].*"$', re.MULTILINE)
        po_files = sorted((ROOT / "po").glob("*.po"))
        self.assertEqual(len(po_files), 28)
        for po_file in po_files:
            subprocess.run(
                [
                    "msgfmt", "--check", "--check-format",
                    "--output-file=/dev/null", str(po_file),
                ],
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
                    f"{po_file.name} contains {selector[2:]} messages",
                )


if __name__ == "__main__":
    unittest.main()
