import gettext
import json
import re
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
PO_DIR = PACKAGE / "po"
LOCALE_DIR = PACKAGE / "locale"
DOMAIN = "anduinos-timeback-machine"
LANGUAGES = {
    "ar", "da", "de", "el", "en_GB", "es", "fi", "fr", "hi",
    "id", "it", "ja", "ko", "nl", "pl", "pt", "pt_BR", "ro",
    "ru", "sv", "th", "tr", "uk", "vi", "zh_CN", "zh_HK",
    "zh_TW",
}
OFFICIAL_DESKTOP_LANGUAGES = LANGUAGES | {"en_US"}
RUST_I18N = re.compile(
    r'\bi18n\s*\(\s*"((?:[^"\\]|\\.)*)"\s*\)',
    re.DOTALL,
)


def source_messages():
    messages = set()
    for source in sorted((PACKAGE / "src").rglob("*.rs")):
        for match in RUST_I18N.finditer(source.read_text(encoding="utf-8")):
            messages.add(json.loads(f'"{match.group(1)}"'))
    return messages


class LocalizationTests(unittest.TestCase):
    def test_language_matrix_and_compiled_catalogs(self):
        self.assertEqual({path.stem for path in PO_DIR.glob("*.po")}, LANGUAGES)
        self.assertEqual(
            {
                path.parent.parent.name
                for path in LOCALE_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.mo")
            },
            LANGUAGES,
        )

    def test_catalog_message_set_matches_rust_source(self):
        catalog_path = LOCALE_DIR / "en_GB" / "LC_MESSAGES" / f"{DOMAIN}.mo"
        with catalog_path.open("rb") as stream:
            catalog = gettext.GNUTranslations(stream)
        catalog_messages = {
            message
            for message in catalog._catalog
            if isinstance(message, str) and message
        }
        self.assertEqual(catalog_messages, source_messages())

    def test_desktop_entry_has_all_28_official_localizations(self):
        desktop = (
            PACKAGE / "data/com.anduinos.timebackmachine.desktop"
        ).read_text(encoding="utf-8")
        for key in ("Name", "GenericName", "Comment", "Keywords"):
            actual = {
                line.removeprefix(f"{key}[").split("]", 1)[0]
                for line in desktop.splitlines()
                if line.startswith(f"{key}[")
            }
            self.assertEqual(actual, OFFICIAL_DESKTOP_LANGUAGES)

    def test_every_catalog_translates_core_interface_text(self):
        for language in sorted(LANGUAGES):
            catalog_path = (
                LOCALE_DIR / language / "LC_MESSAGES" / f"{DOMAIN}.mo"
            )
            with self.subTest(language=language), catalog_path.open("rb") as stream:
                catalog = gettext.GNUTranslations(stream)
                translations = [
                    catalog.gettext("Overview"),
                    catalog.gettext("Recovery Points"),
                    catalog.gettext("Create Recovery Point"),
                ]
                self.assertTrue(all(translations))
                if language != "en_GB":
                    self.assertTrue(
                        any(
                            translated != source
                            for translated, source in zip(
                                translations,
                                (
                                    "Overview",
                                    "Recovery Points",
                                    "Create Recovery Point",
                                ),
                            )
                        )
                    )


if __name__ == "__main__":
    unittest.main()
