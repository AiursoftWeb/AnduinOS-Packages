import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FirefoxAppStreamTests(unittest.TestCase):
    def test_component_uses_the_existing_visible_launcher(self):
        component = ET.parse(ROOT / "data/firefox.metainfo.xml").getroot()
        self.assertEqual(component.findtext("id"), "firefox.desktop")
        launchable = component.find("launchable[@type='desktop-id']")
        self.assertIsNotNone(launchable)
        self.assertEqual(launchable.text, "firefox.desktop")

    def test_helper_is_hidden_and_does_not_replace_upstream_launcher(self):
        helper = (ROOT / "data/firefox.desktop.desktop").read_text()
        project = (ROOT / "firefox-anduinos.aosproj").read_text()
        self.assertIn("NoDisplay=true", helper)
        self.assertIn("X-AppStream-Ignore=true", helper)
        self.assertIn('Include="data/firefox.desktop.desktop"', project)
        self.assertNotIn('Target="/usr/share/applications/firefox.desktop"', project)
        self.assertTrue((ROOT / "data/firefox.svg").is_file())
        self.assertTrue((ROOT / "screenshots/browser.png").is_file())


if __name__ == "__main__":
    unittest.main()
