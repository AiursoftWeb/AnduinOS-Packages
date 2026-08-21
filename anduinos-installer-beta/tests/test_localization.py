import ast
import gettext
import unittest
from pathlib import Path

from i18n import DOMAIN, _, clear_translation_cache
from languages import DEFAULT_LANGUAGE, KEYBOARD_LAYOUTS, LANGUAGES


PACKAGE = Path(__file__).resolve().parents[1]
PO_DIR = PACKAGE / "po"
LOCALE_DIR = PACKAGE / "locale"


class LocalizationTests(unittest.TestCase):
    def tearDown(self):
        clear_translation_cache()

    def test_every_non_source_language_has_po_and_compiled_catalog(self):
        expected = {
            language.code
            for language in LANGUAGES
            if language.code != DEFAULT_LANGUAGE
        }
        po_languages = {path.stem for path in PO_DIR.glob("*.po")}
        compiled_languages = {
            path.parent.parent.name
            for path in LOCALE_DIR.glob(f"*/LC_MESSAGES/{DOMAIN}.mo")
        }

        self.assertEqual(po_languages, expected)
        self.assertEqual(compiled_languages, expected)

    def test_every_catalog_loads_and_translates_interface_text(self):
        for language in LANGUAGES:
            if language.code == DEFAULT_LANGUAGE:
                continue
            with self.subTest(language=language.code):
                catalog_path = (
                    LOCALE_DIR
                    / language.code
                    / "LC_MESSAGES"
                    / f"{DOMAIN}.mo"
                )
                with catalog_path.open("rb") as stream:
                    catalog = gettext.GNUTranslations(stream)
                translations = [
                    catalog.gettext("Next"),
                    catalog.gettext("Installation Complete"),
                    catalog.gettext("Select Timezone"),
                ]
                self.assertTrue(all(translations))
                if language.code != "en_GB":
                    self.assertTrue(
                        any(
                            translated != source
                            for translated, source in zip(
                                translations,
                                (
                                    "Next",
                                    "Installation Complete",
                                    "Select Timezone",
                                ),
                            )
                        )
                    )

    def test_runtime_language_selection_uses_selected_catalog(self):
        clear_translation_cache()
        self.assertEqual(_("Next", DEFAULT_LANGUAGE), "Next")
        self.assertNotEqual(_("Next", "zh_CN"), "Next")
        self.assertNotEqual(_("Next", "de"), "Next")

    def test_catalog_message_set_matches_source_and_policy(self):
        source_messages = set(KEYBOARD_LAYOUTS.values())
        for source in sorted((PACKAGE / "src").rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else None
                    )
                )
                expressions = []
                if name in {
                    "_",
                    "N_",
                    "_page_title",
                    "_page_subtitle",
                    "_nav_btn",
                }:
                    expressions.append(node.args[0])
                elif name == "_page_header":
                    expressions.extend(node.args[:2])
                elif name == "_nav_box":
                    expressions.extend(
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "next_label"
                    )
                for expression in expressions:
                    source_messages.update(
                        self._literal_messages(expression)
                    )

        catalog_path = (
            LOCALE_DIR / "en_GB" / "LC_MESSAGES" / f"{DOMAIN}.mo"
        )
        with catalog_path.open("rb") as stream:
            catalog = gettext.GNUTranslations(stream)
        catalog_messages = {
            message
            for message in catalog._catalog
            if isinstance(message, str) and message
        }
        self.assertEqual(catalog_messages, source_messages)

    @classmethod
    def _literal_messages(cls, expression):
        if isinstance(expression, ast.Constant) and isinstance(
            expression.value, str
        ):
            return {expression.value}
        if isinstance(expression, ast.IfExp):
            return cls._literal_messages(
                expression.body
            ) | cls._literal_messages(expression.orelse)
        return set()
