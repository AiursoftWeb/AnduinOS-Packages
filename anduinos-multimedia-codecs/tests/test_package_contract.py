from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

class PackageContractTests(unittest.TestCase):
    def test_extended_codecs(self):
        project = ET.parse(ROOT / "anduinos-multimedia-codecs.aosproj").getroot()
        self.assertEqual({item.get("Include") for item in project.iter("Dependency")},
                         {"gstreamer1.0-plugins-bad", "gstreamer1.0-plugins-ugly", "gstreamer1.0-libav", "libavcodec-extra"})


if __name__ == "__main__":
    unittest.main()
