import configparser
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PROJECT_FILE = PROJECT / "anduinos-mimeapps.aosproj"
MIMEAPPS_FILE = PROJECT / "assets/anduinos-mimeapps.list"
APPIMAGE_RUNNER_FILE = PROJECT / "assets/com.anduinos.AppImageRunner.desktop"
EXE_RUNNER = "com.anduinos.ExeRunner.desktop;"
APPIMAGE_RUNNER = "com.anduinos.AppImageRunner.desktop;"
TEXT_EDITOR = "org.gnome.TextEditor.desktop;"
EXPECTED_WINDOWS_MIME_TYPES = {
    "application/x-msdownload",
    "application/vnd.microsoft.portable-executable",
    "application/x-msi",
}
EXPECTED_APPIMAGE_MIME_TYPES = {
    "application/vnd.appimage",
    "application/x-iso9660-appimage",
}


class MimeAppsPackageContractTests(unittest.TestCase):
    def test_package_revision_and_contract_test_are_wired(self):
        project = ET.parse(PROJECT_FILE).getroot()
        self.assertEqual(
            "2.0.1-5+$(SuiteShortName)",
            project.findtext(".//PackageVersion"),
        )
        command = project.find(".//PrebuildCommand")
        self.assertIsNotNone(command)
        self.assertEqual("python3 tests/test_package_contract.py", command.get("Run"))

    def test_every_supported_windows_mime_defaults_to_exe_runner(self):
        associations = {}
        for raw_line in MIMEAPPS_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if (
                not line
                or line.startswith("#")
                or (line.startswith("[") and line.endswith("]"))
            ):
                continue
            mime_type, separator, desktop = line.partition("=")
            self.assertTrue(separator, raw_line)
            self.assertNotIn(mime_type, associations)
            associations[mime_type] = desktop

        actual = {
            mime_type
            for mime_type, desktop in associations.items()
            if desktop == EXE_RUNNER
        }
        self.assertEqual(EXPECTED_WINDOWS_MIME_TYPES, actual)

    def test_appimage_defaults_are_narrow_and_have_a_real_handler(self):
        associations = {}
        for raw_line in MIMEAPPS_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if (
                not line
                or line.startswith("#")
                or (line.startswith("[") and line.endswith("]"))
            ):
                continue
            mime_type, separator, desktop = line.partition("=")
            self.assertTrue(separator, raw_line)
            associations[mime_type] = desktop

        actual = {
            mime_type
            for mime_type, desktop in associations.items()
            if desktop == APPIMAGE_RUNNER
        }
        self.assertEqual(EXPECTED_APPIMAGE_MIME_TYPES, actual)
        self.assertNotIn("application/x-executable", actual)
        self.assertNotIn("application/x-pie-executable", actual)

        project = ET.parse(PROJECT_FILE).getroot()
        included = {
            item.get("Target"): item.get("Include")
            for item in project.findall(".//IncludeFile")
        }
        self.assertEqual(
            "assets/com.anduinos.AppImageRunner.desktop",
            included["/usr/share/applications/com.anduinos.AppImageRunner.desktop"],
        )

        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read(APPIMAGE_RUNNER_FILE, encoding="utf-8")
        entry = parser["Desktop Entry"]
        advertised = {item for item in entry["MimeType"].split(";") if item}
        self.assertEqual(EXPECTED_APPIMAGE_MIME_TYPES, advertised)
        self.assertEqual("/usr/bin/env -- %f", entry["Exec"])
        self.assertEqual("true", entry["NoDisplay"])

    def test_plain_text_and_markdown_default_to_text_editor(self):
        associations = {}
        for raw_line in MIMEAPPS_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if (
                not line
                or line.startswith("#")
                or (line.startswith("[") and line.endswith("]"))
            ):
                continue
            mime_type, separator, desktop = line.partition("=")
            self.assertTrue(separator, raw_line)
            associations[mime_type] = desktop

        self.assertEqual(TEXT_EDITOR, associations["text/plain"])
        self.assertEqual(TEXT_EDITOR, associations["text/markdown"])


if __name__ == "__main__":
    unittest.main()
