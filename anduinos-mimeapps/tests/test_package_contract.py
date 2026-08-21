import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PROJECT_FILE = PROJECT / "anduinos-mimeapps.aosproj"
MIMEAPPS_FILE = PROJECT / "assets/anduinos-mimeapps.list"
EXE_RUNNER = "com.anduinos.ExeRunner.desktop;"
TEXT_EDITOR = "org.gnome.TextEditor.desktop;"
EXPECTED_WINDOWS_MIME_TYPES = {
    "application/x-msdownload",
    "application/vnd.microsoft.portable-executable",
    "application/x-msi",
}


class MimeAppsPackageContractTests(unittest.TestCase):
    def test_package_revision_and_contract_test_are_wired(self):
        project = ET.parse(PROJECT_FILE).getroot()
        self.assertEqual(
            "2.0.2-1+$(SuiteShortName)",
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

    def test_package_does_not_claim_appimage_execution(self):
        content = MIMEAPPS_FILE.read_text(encoding="utf-8")
        for mime_type in (
            "application/vnd.appimage",
            "application/x-iso9660-appimage",
        ):
            self.assertNotIn(f"{mime_type}=", content)

        project = ET.parse(PROJECT_FILE).getroot()
        included = {
            item.get("Include") for item in project.findall(".//IncludeFile")
        }
        self.assertNotIn(
            "assets/com.anduinos.AppImageRunner.desktop",
            included,
        )
        self.assertFalse(
            (PROJECT / "assets/com.anduinos.AppImageRunner.desktop").exists()
        )

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
