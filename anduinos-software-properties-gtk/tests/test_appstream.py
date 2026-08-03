import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SoftwarePropertiesAppStreamTests(unittest.TestCase):
    def test_component_claims_the_real_gtk_launcher(self):
        component = ET.parse(
            ROOT / "data/software-properties-gtk.metainfo.xml"
        ).getroot()
        self.assertEqual(component.findtext("id"), "software-properties-gtk.desktop")
        launchable = component.find("launchable[@type='desktop-id']")
        self.assertIsNotNone(launchable)
        self.assertEqual(launchable.text, "software-properties-gtk.desktop")

    def test_helper_is_hidden_and_additional_drivers_stays_in_upstream_payload(self):
        helper = (
            ROOT / "data/software-properties-gtk.desktop.desktop"
        ).read_text()
        project = (ROOT / "anduinos-software-properties-gtk.aosproj").read_text()
        self.assertIn("NoDisplay=true", helper)
        self.assertIn("X-AppStream-Ignore=true", helper)
        self.assertIn(
            'Include="data/software-properties-gtk.desktop.desktop"',
            project,
        )
        self.assertNotIn(
            'Target="/usr/share/applications/software-properties-gtk.desktop"',
            project,
        )
        self.assertIn(
            "-name 'software-properties-gtk.appdata.xml' -delete",
            project,
        )
        self.assertTrue((ROOT / "data/software-properties.svg").is_file())
        self.assertTrue((ROOT / "screenshots/software-sources.png").is_file())
        self.assertTrue((ROOT / "screenshots/additional-drivers.png").is_file())


if __name__ == "__main__":
    unittest.main()
