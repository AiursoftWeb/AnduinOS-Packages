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
        self.assertFalse(any("anduinos-whisper" in name for name in dependencies))


    def test_remote_access_and_extra_codecs_are_optional(self):
        project = ET.parse(next(ROOT.glob("*.aosproj"))).getroot()
        automatic = {item.get("Include") for tag in ("Dependency", "Recommend") for item in project.iter(tag)}
        self.assertNotIn("openssh-server", automatic)
        self.assertIn("openssh-server", {item.get("Include") for item in project.iter("Suggest")})
        self.assertTrue({"gstreamer1.0-plugins-bad", "gstreamer1.0-plugins-ugly", "gstreamer1.0-libav", "libavcodec-extra"}.isdisjoint(automatic))


if __name__ == "__main__":
    unittest.main()
