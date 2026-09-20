import pathlib
import unittest
import xml.etree.ElementTree as ET


ROOT = pathlib.Path(__file__).parents[1]
SCRIPT = ROOT / "assets" / "anduinos-oobe"
PROJECT = ROOT / "anduinos-oobe.aosproj"


class SecurityPageTests(unittest.TestCase):
    def test_oobe_does_not_ask_about_advanced_mdns_or_bash_preferences(self):
        source = SCRIPT.read_text()

        self.assertNotIn("Local Network Discovery (mDNS)", source)
        self.assertNotIn("Bash Command Predictions", source)
        self.assertNotIn("get_mdns_control_state", source)
        self.assertNotIn("bash_command_predictions_enabled", source)

    def test_package_has_no_oobe_specific_backends_for_removed_controls(self):
        project = ET.parse(PROJECT).getroot()
        includes = {
            node.attrib.get("Include")
            for node in project.findall(".//*[@Include]")
        }

        self.assertNotIn("../lib/bash_prediction_settings.py", includes)
        self.assertNotIn("scripts/network-service-helper", includes)
        self.assertNotIn("data/com.anduinos.oobe.policy", includes)
        self.assertFalse((ROOT / "scripts" / "network-service-helper").exists())
        self.assertFalse((ROOT / "data" / "com.anduinos.oobe.policy").exists())


if __name__ == "__main__":
    unittest.main()
