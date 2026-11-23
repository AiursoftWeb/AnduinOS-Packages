import unittest
from pathlib import Path

from languages import LANGUAGES

PACKAGE = Path(__file__).resolve().parents[1]

class LiveShortcutAssetTests(unittest.TestCase):

    def test_desktop_launcher_has_all_official_localizations(self):
        launcher = (
            PACKAGE / "assets/anduinos-installer-beta.desktop"
        ).read_text()
        expected = {language.code for language in LANGUAGES}
        for key in ("Name", "Comment"):
            actual = {
                line.removeprefix(f"{key}[").split("]", 1)[0]
                for line in launcher.splitlines()
                if line.startswith(f"{key}[")
            }
            self.assertEqual(actual, expected)
