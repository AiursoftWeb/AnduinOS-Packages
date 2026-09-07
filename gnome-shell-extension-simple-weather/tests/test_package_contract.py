from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

class PackageContractTests(unittest.TestCase):
    def test_defaults_preserve_activation_without_fixed_location(self):
        defaults = (ROOT / "dconf/18-simple-weather.conf").read_text()
        self.assertIn("is-activated=true", defaults)
        self.assertIn("my-loc-provider='disable'", defaults)
        self.assertNotIn("locations=", defaults)


if __name__ == "__main__":
    unittest.main()
