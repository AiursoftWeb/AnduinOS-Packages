from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

class PackageTests(unittest.TestCase):
    def test_all_python_sources_compile_without_creating_cache_files(self):
        sources = [
            *ROOT.glob("scripts/*"),
            *ROOT.glob("src/anduinos_driver_center/*.py"),
        ]
        for source in sources:
            if not source.is_file():
                continue
            compile(source.read_text(), str(source), "exec")

    def test_driver_illustrations_are_parseable_local_svg_files(self):
        resources = list((ROOT / "resources").glob("*.svg"))
        self.assertTrue(resources, "No SVG resources were found for validation")
        for path in resources:
            root = ET.parse(path).getroot()
            self.assertTrue(root.tag.endswith("svg"))

    def test_polkit_only_authorizes_the_fixed_helper(self):
        tree = ET.parse(ROOT / "data/com.anduinos.DriverCenter.policy")
        paths = {
            node.text.strip() for node in tree.findall(".//annotate")
            if node.attrib.get("key") == "org.freedesktop.policykit.exec.path"
        }
        self.assertEqual(paths, {
            "/usr/libexec/anduinos-driver-center/driver-helper",
            "/usr/libexec/anduinos-driver-center/memory-helper",
        })
        self.assertFalse(tree.findall(".//annotate[@key='org.freedesktop.policykit.exec.allow_gui']"))

    def test_every_supported_locale_is_complete_and_matches_the_ui(self):
        expected_locales = {
            "ar", "da", "de", "el", "en_GB", "en_US", "es", "fi", "fr",
            "hi", "id", "it", "ja", "ko", "nl", "pl", "pt", "pt_BR",
            "ro", "ru", "sv", "th", "tr", "uk", "vi", "zh_CN", "zh_HK",
            "zh_TW",
        }
        po_files = sorted((ROOT / "po").glob("*.po"))
        self.assertEqual({path.stem for path in po_files}, expected_locales)

        toolkit_ui = ROOT.parent / "anduinos-secureboot-toolkit" / "src" / "anduinos_secureboot" / "ui.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            extracted = Path(temporary_directory) / "messages.pot"
            subprocess.run(
                [
                    "xgettext", "--language=Python", "--keyword=_",
                    "--keyword=ngettext:1,2", "--from-code=UTF-8",
                    f"--output={extracted}",
                    str(ROOT / "src" / "anduinos_driver_center" / "app.py"),
                    str(ROOT / "src" / "anduinos_driver_center" / "computer_ui.py"),
                    str(toolkit_ui),
                ],
                check=True,
            )
            # The catalog also contains desktop and polkit messages. Every UI
            # message must be present, but those non-Python entries are valid.
            subprocess.run(
                ["msgcmp", "--use-untranslated", "--no-fuzzy-matching",
                 str(ROOT / "po" / "anduinos-driver-center.pot"), str(extracted)],
                check=True,
                capture_output=True,
                text=True,
            )

        for po_file in po_files:
            subprocess.run(
                ["msgfmt", "--check", "--check-format", "--output-file=/dev/null", str(po_file)],
                check=True,
            )
            if po_file.stem != "en_US":
                subprocess.run(
                    ["msgcmp", "--use-untranslated", "--no-fuzzy-matching",
                     str(po_file), str(ROOT / "po" / "anduinos-driver-center.pot")],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            for selector in ("--untranslated", "--only-fuzzy"):
                result = subprocess.run(
                    ["msgattrib", selector, "--no-obsolete", str(po_file)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if po_file.stem != "en_US":
                    message_count = sum(
                        line.startswith("msgid ")
                        for line in result.stdout.splitlines()
                    )
                    self.assertLessEqual(
                        message_count,
                        1,
                        f"{po_file.name} contains {selector.removeprefix('--')} messages",
                    )

if __name__ == "__main__":
    unittest.main()
