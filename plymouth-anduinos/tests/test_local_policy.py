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
        self.assertIn("anduinos-core-system (>= 2.0.2-3)", dependencies)
        for name in ["postinst.sh","prerm.sh"]:
            content = (ROOT / "scripts" / name).read_text()
            self.assertIn("anduinos-dracut-verify --rebuild", content)
            self.assertNotRegex(content, r"dracut[^\n]*(?:\|\|\s*true|2>/dev/null)")


if __name__ == "__main__":
    unittest.main()
