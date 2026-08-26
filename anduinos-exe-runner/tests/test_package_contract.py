import configparser
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PROJECT_FILE = PROJECT / "anduinos-exe-runner.aosproj"
DESKTOP_FILE = PROJECT / "data/com.anduinos.ExeRunner.desktop"
RUNNER_FILE = PROJECT / "assets/anduinos-exe-runner"
CONFIGURATOR_FILE = PROJECT / "assets/configure-bottles.py"
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

    def test_bottles_configurator_is_packaged(self):
        project = ET.parse(PROJECT_FILE).getroot()
        packaged_files = {
            (item.get("Include"), item.get("Target"))
            for item in project.findall(".//IncludeFile")
        }
        self.assertIn(
            (
                "assets/configure-bottles.py",
                "/usr/share/anduinos-exe-runner/configure-bottles.py",
            ),
            packaged_files,
        )

    def test_configurator_matches_current_bottles_cli_initialization(self):
        runner = RUNNER_FILE.read_text(encoding="utf-8")
        configurator = CONFIGURATOR_FILE.read_text(encoding="utf-8")

        self.assertNotIn("RunAsync", runner)
        self.assertNotIn("Manager(False", runner)
        self.assertNotIn("RunAsync", configurator)
        self.assertNotIn("Manager(False", configurator)
        self.assertIn(
            "Manager(g_settings=Gio.Settings.new(APP_ID), is_cli=True)",
            configurator,
        )
        self.assertIn("Events.ComponentsOrganizing", configurator)
        self.assertIn("Events.DependenciesOrganizing", configurator)
        self.assertIn("Events.InstallersOrganizing", configurator)
        self.assertIn('(\"cjkfonts\", \"allfonts\")', configurator)


if __name__ == "__main__":
    unittest.main()
