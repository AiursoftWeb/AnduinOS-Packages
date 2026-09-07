#!/usr/bin/env python3
"""Package-local boot policy checks."""
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

class LocalPolicyTests(unittest.TestCase):
    def test_own_package_contract(self):
        project = ET.parse(next(ROOT.glob("*.aosproj"))).getroot()
        dependencies = {item.get("Include") for item in project.iter("Dependency")}
        self.assertIn("anduinos-control-panel", {item.get("Include") for item in project.iter("Recommend")})


    def test_media_applications_are_recommended(self):
        project = ET.parse(next(ROOT.glob("*.aosproj"))).getroot()
        recommendations = {item.get("Include") for item in project.iter("Recommend")}
        self.assertTrue({"celluloid", "ffmpegthumbnailer"} <= recommendations)


if __name__ == "__main__":
    unittest.main()
