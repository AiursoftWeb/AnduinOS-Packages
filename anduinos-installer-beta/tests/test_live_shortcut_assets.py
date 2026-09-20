import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]

class LiveShortcutAssetTests(unittest.TestCase):

    def test_desktop_launcher_has_all_28_official_localizations(self):
        launcher = (
            PACKAGE / "assets/anduinos-installer-beta.desktop"
        ).read_text()
        expected = {
            "ar",
            "da",
            "de",
            "el",
            "en_GB",
            "en_US",
            "es",
            "fi",
            "fr",
            "hi",
            "id",
            "it",
            "ja",
            "ko",
            "nl",
            "pl",
            "pt",
            "pt_BR",
            "ro",
            "ru",
            "sv",
            "th",
            "tr",
            "uk",
            "vi",
            "zh_CN",
            "zh_HK",
            "zh_TW",
        }
        for key in ("Name", "Comment"):
            actual = {
                line.removeprefix(f"{key}[").split("]", 1)[0]
                for line in launcher.splitlines()
                if line.startswith(f"{key}[")
            }
            self.assertEqual(actual, expected)
