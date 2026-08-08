from pathlib import Path
from importlib.machinery import SourceFileLoader
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
sys.path.insert(0, str(REPOSITORY / "anduinos-secureboot-toolkit" / "src"))
sys.path.insert(0, str(REPOSITORY / "anduinos-driver-center" / "src"))
from anduinos_driver_center.core import XboxState, XboxStatus  # noqa: E402
from anduinos_secureboot import SecureBootState, SecureBootStatus  # noqa: E402

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

    def test_oobe_omits_only_known_non_enforcing_secure_boot_states(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        guard = application.index(
            "if not _inspect_secure_boot().enforcement_inactive:"
        )
        page = application.index("factories.append(lambda: create_secureboot_page", guard)
        next_hardware_page = application.index("if has_nvidia_gpu():", guard)
        self.assertLess(guard, page)
        self.assertLess(page, next_hardware_page)

    def test_xbox_driver_workflow_fails_closed_on_unknown_state(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        self.assertIn("if not trust.state_known:", application)
        self.assertIn("_XboxStatus.SECURE_BOOT_UNKNOWN", application)
        self.assertIn("return 'refresh'", application)

    def test_oobe_reuses_driver_center_xbox_health_model(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        self.assertIn("XboxStatus as _XboxStatus", application)
        self.assertIn("xbox = _inspect_xbox(trust)", application)
        for status in (
            "MODULE_MISSING",
            "SECURE_BOOT_UNKNOWN",
            "ENROLLMENT_PENDING",
            "TRUST_SETUP_REQUIRED",
            "SIGNATURE_MISMATCH",
            "LOAD_STATE_UNKNOWN",
            "LOADED",
            "READY",
        ):
            self.assertIn(f"_XboxStatus.{status}", application)

    def test_every_xbox_warning_has_an_enabled_recovery_path(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        self.assertIn("def _xbox_recovery_action", application)
        self.assertIn("_apply_recovery(trust, xbox, button)", application)
        self.assertNotIn("install_btn.set_sensitive(False)", application[
            application.index("def create_xbox_page"):
            application.index("def create_exe_sandbox_page")
        ])
        self.assertEqual(
            application.count("xb_refresh_btn.connect('clicked', _on_xb_refresh)"),
            1,
        )

    def test_every_non_ready_xbox_state_has_one_recovery_action(self):
        trust = SecureBootState(True, True, True, True, "aa12")
        expected = {
            XboxStatus.MODULE_MISSING: "reinstall",
            XboxStatus.SECURE_BOOT_UNKNOWN: "refresh",
            XboxStatus.ENROLLMENT_PENDING: "reboot",
            XboxStatus.TRUST_SETUP_REQUIRED: "trust",
            XboxStatus.SIGNATURE_MISMATCH: "reinstall",
            XboxStatus.LOAD_STATE_UNKNOWN: "refresh",
            XboxStatus.LOADED: None,
            XboxStatus.READY: None,
        }
        for status, action in expected.items():
            with self.subTest(status=status):
                xbox = XboxState(status, True, True, False, "aa12", True)
                self.assertEqual(OOBE._xbox_recovery_action(trust, xbox), action)

    def test_not_installed_xbox_recovery_respects_trust_state(self):
        xbox = XboxState(XboxStatus.NOT_INSTALLED, False, False, False, None, False)
        states = (
            (
                SecureBootState(
                    False, False, False, False, None,
                    status=SecureBootStatus.UNKNOWN,
                ),
                "refresh",
            ),
            (SecureBootState(True, True, True, False, "aa", True), "reboot"),
            (SecureBootState(True, True, True, False, "aa"), "trust"),
            (SecureBootState(False, False, False, False, None), "install"),
        )
        for trust, action in states:
            with self.subTest(action=action):
                self.assertEqual(OOBE._xbox_recovery_action(trust, xbox), action)

    def test_partial_secure_boot_success_is_not_reported_as_total_failure(self):
        payload = {
            "steps": {
                "key_created": {"status": "success"},
                "enrollment_queued": {"status": "success"},
                "modules_rebuilt": {"status": "failed"},
            }
        }
        self.assertTrue(OOBE._secure_boot_trust_prepared(payload))

    def test_driver_operations_use_restricted_helper_and_do_not_fake_success(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        hardware = application[
            application.index("def create_nvidia_page"):
            application.index("def create_exe_sandbox_page")
        ]
        self.assertNotIn("ubuntu-drivers install || true", hardware)
        self.assertNotIn("['pkexec', 'sh', '-c'", hardware)
        self.assertIn("['pkexec', DRIVER_CENTER_HELPER, *helper_arguments]", hardware)
        self.assertIn("['pkexec', DRIVER_CENTER_HELPER, 'install-xbox']", hardware)
        self.assertIn("['pkexec', DRIVER_CENTER_HELPER, 'repair-xbox']", hardware)
        self.assertIn("_('  Checking...  ')", hardware)

    def test_oobe_declares_shared_and_hardware_dependencies(self):
        project = ET.parse(ROOT / "anduinos-oobe.aosproj").getroot()
        dependencies = {item.get("Include") for item in project.iter("Dependency")}
        self.assertTrue(
            {
                "anduinos-secureboot-toolkit",
                "anduinos-driver-center (>= 2.0.0-8)",
                "ubuntu-drivers-common",
                "pciutils",
            }
            <= dependencies
        )

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
