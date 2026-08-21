from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class AppearanceIntegrationTests(unittest.TestCase):
    def test_oobe_uses_the_shared_appearance_api(self):
        application = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")

        self.assertIn("from anduinos_appearance import (", application)
        self.assertIn("apply_style_and_position(OOBE_LAYOUT_STYLES[style]", application)
        self.assertIn("draw_preview(", application)
        self.assertIn("detect_current()", application)
        self.assertNotIn("def _apply_layout", application)
        self.assertNotIn("SourceFileLoader", application)
        self.assertNotIn("_get_appearance_module", application)
        self.assertNotIn("panel-element-positions", application)

    def test_oobe_requires_the_shared_api_package_version(self):
        project = ET.parse(ROOT / "anduinos-oobe.aosproj").getroot()
        dependencies = {item.get("Include") for item in project.iter("Dependency")}

        self.assertIn("anduinos-appearance (>= 2.0.1-5)", dependencies)


if __name__ == "__main__":
    unittest.main()
