import configparser
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PROJECT_FILE = PROJECT / "anduinos-exe-runner.aosproj"
DESKTOP_FILE = PROJECT / "data/com.anduinos.ExeRunner.desktop"
EXPECTED_MIME_TYPES = {
    "application/x-msdownload",
    "application/vnd.microsoft.portable-executable",
    "application/x-msi",
}


class ExeRunnerPackageContractTests(unittest.TestCase):
    def test_package_revision_and_contract_test_are_wired(self):
        project = ET.parse(PROJECT_FILE).getroot()
        commands = {
            item.get("Run") for item in project.findall(".//PrebuildCommand")
        }
        self.assertIn("python3 tests/test_package_contract.py", commands)

    def test_desktop_file_advertises_every_supported_windows_mime_type(self):
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read(DESKTOP_FILE, encoding="utf-8")
        entry = parser["Desktop Entry"]
        mime_types = {item for item in entry["MimeType"].split(";") if item}
        self.assertEqual(EXPECTED_MIME_TYPES, mime_types)
        self.assertEqual("/usr/bin/anduinos-exe-runner %f", entry["Exec"])
        self.assertEqual("true", entry["NoDisplay"])


if __name__ == "__main__":
    unittest.main()
