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
        self.assertIn("anduinos-dracut-migration", dependencies)
        self.assertNotIn("anduinos-no-snapd", dependencies)
        self.assertIn("anduinos-no-snapd", {item.get("Include") for item in project.iter("Recommend")})
        self.assertFalse(any("anduinos-whisper" in name for name in dependencies))

    def test_desktop_recommends_the_policy_only_on_resolute(self):
        desktop = ET.parse(ROOT / 'anduinos-desktop.aosproj').getroot()
        matching_dependencies = desktop.findall(
            ".//Dependency[@Include='anduinos-kernel-parameters']"
        )
        matching_recommendations = desktop.findall(
            ".//Recommend[@Include='anduinos-kernel-parameters']"
        )
        self.assertEqual(matching_dependencies, [])
        self.assertEqual(len(matching_recommendations), 1)
        self.assertEqual(
            matching_recommendations[0].get("Condition"),
            "'$(Suite)' == 'resolute-addon'",
        )


if __name__ == "__main__":
    unittest.main()
