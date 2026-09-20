from pathlib import Path
from importlib.machinery import SourceFileLoader
import re
import subprocess
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OOBE = SourceFileLoader(
    "anduinos_oobe_behavior", str(ROOT / "assets/anduinos-oobe")
).load_module()

class SecureBootToolkitTests(unittest.TestCase):

    def test_open_driver_center_launches_without_elevation_and_stays(self):
        with mock.patch.object(OOBE.subprocess, "Popen") as popen:
            error = OOBE._open_driver_center()

        self.assertIsNone(error)
        popen.assert_called_once_with(["/usr/bin/anduinos-driver-center"])

    def test_open_driver_center_failure_stays_on_page_with_localized_error(self):
        with mock.patch.object(
            OOBE.subprocess,
            "Popen",
            side_effect=FileNotFoundError("missing executable"),
        ):
            error = OOBE._open_driver_center()

        self.assertEqual(
            error,
            OOBE._("Could not open AnduinOS Driver Center: {}").format(
                "missing executable"
            ),
        )

    def test_skip_navigates_without_starting_driver_center(self):
        navigate_next = mock.Mock()
        with mock.patch.object(OOBE.subprocess, "Popen") as popen:
            OOBE._skip_hardware_drivers(navigate_next)

        popen.assert_not_called()
        navigate_next.assert_called_once_with()

    def test_oobe_catalog_matches_oobe_and_secure_boot_ui(self):
        toolkit_ui = (
            ROOT.parent
            / "anduinos-secureboot-toolkit"
            / "src"
            / "anduinos_secureboot"
            / "ui.py"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            extracted = Path(temporary_directory) / "messages.pot"
            metadata_messages = set()
            for folder in (ROOT / "assets", ROOT / "data"):
                for desktop in folder.rglob("*.desktop"):
                    for line in desktop.read_text(encoding="utf-8").splitlines():
                        key, separator, value = line.partition("=")
                        if separator and value and key in {"Name", "GenericName", "Comment", "Keywords"}:
                            metadata_messages.add(value)
                for policy in folder.rglob("*.policy"):
                    for action in ET.parse(policy).getroot().findall("action"):
                        for tag in ("description", "message"):
                            for element in action.findall(tag):
                                if "{http://www.w3.org/XML/1998/namespace}lang" not in element.attrib:
                                    value = (element.text or "").strip()
                                    if value:
                                        metadata_messages.add(value)
            metadata_source = Path(temporary_directory) / "metadata.py"
            metadata_source.write_text(
                "\n".join(f"_({message!r})" for message in sorted(metadata_messages)) + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    "xgettext",
                    "--language=Python",
                    "--keyword=_",
                    "--keyword=ngettext:1,2",
                    "--from-code=UTF-8",
                    f"--output={extracted}",
                    str(ROOT / "assets" / "anduinos-oobe"),
                    str(toolkit_ui),
                    str(metadata_source),
                ],
                check=True,
            )
            template_difference = subprocess.run(
                [
                    "msgcomm",
                    "--less-than=2",
                    "--omit-header",
                    str(ROOT / "po" / "anduinos-oobe.pot"),
                    str(extracted),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(template_difference.stdout.strip(), "")

    def test_all_oobe_catalogs_are_complete_and_format_safe(self):
        translated_msgid = re.compile(r'^msgid "[^"].*"$', re.MULTILINE)
        po_files = sorted((ROOT / "po").glob("*.po"))
        self.assertEqual(len(po_files), 28)
        for po_file in po_files:
            subprocess.run(
                [
                    "msgfmt", "--check", "--check-format",
                    "--output-file=/dev/null", str(po_file),
                ],
                check=True,
            )
            for selector in ("--untranslated", "--only-fuzzy"):
                result = subprocess.run(
                    ["msgattrib", selector, "--no-obsolete", str(po_file)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertIsNone(
                    translated_msgid.search(result.stdout),
                    f"{po_file.name} contains {selector[2:]} messages",
                )

if __name__ == "__main__":
    unittest.main()
