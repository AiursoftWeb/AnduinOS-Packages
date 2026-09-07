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
        self.assertIn("libggml0-backend-vulkan (>= 0.9.11)", {item.get("Include") for item in project.iter("Recommend")})
        self.assertFalse(any("libggml0-backend-vulkan" in name for name in dependencies))


if __name__ == "__main__":
    unittest.main()
