from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class SecureBootToolkitTests(unittest.TestCase):
    def test_oobe_embeds_the_shared_secure_boot_page(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        self.assertIn("_shared_secure_boot_page", application)
        self.assertNotIn("dkms autoinstall", application)
        self.assertNotIn("update-secureboot-policy --new-key", application)
        self.assertNotIn("['mokutil'", application)
        self.assertNotIn("['openssl'", application)
        self.assertNotIn("['modinfo'", application)

    def test_oobe_omits_secure_boot_page_when_firmware_disables_it(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")
        guard = application.index("if _inspect_secure_boot().enabled:")
        page = application.index("factories.append(lambda: create_secureboot_page", guard)
        next_hardware_page = application.index("if has_nvidia_gpu():", guard)
        self.assertLess(guard, page)
        self.assertLess(page, next_hardware_page)

    def test_oobe_declares_shared_and_hardware_dependencies(self):
        project = ET.parse(ROOT / "anduinos-oobe.aosproj").getroot()
        dependencies = {item.get("Include") for item in project.iter("Dependency")}
        self.assertTrue(
            {
                "anduinos-secureboot-toolkit",
                "ubuntu-drivers-common",
                "pciutils",
            }
            <= dependencies
        )


if __name__ == "__main__":
    unittest.main()
