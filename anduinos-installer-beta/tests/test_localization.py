import ast
import gettext
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from i18n import DOMAIN, _, clear_translation_cache
from languages import DEFAULT_LANGUAGE, KEYBOARD_LAYOUTS, LANGUAGES
from installer_core.ntfs_resize import NtfsResizeBlockReason


PACKAGE = Path(__file__).resolve().parents[1]
PO_DIR = PACKAGE / "po"
LOCALE_DIR = PACKAGE / "locale"


class LocalizationTests(unittest.TestCase):
    def test_disk_check_error_uses_selected_language_instead_of_raw_force_advice(self):
        tree = ast.parse((PACKAGE / "src/pages.py").read_text(encoding="utf-8"))
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "_resize_block_message")
        code = compile(ast.Module(body=[function], type_ignores=[]), "pages.py", "exec")
        source = (
            "This NTFS volume requires a disk check. In Windows, back up "
            "important files and run 'chkdsk X: /f' as administrator "
            "(replace X with this volume's drive letter). If prompted, "
            "schedule the check, then restart into Windows. Let the check "
            "finish and fully shut down Windows before trying again."
        )
        inspection = SimpleNamespace(
            block_reason=NtfsResizeBlockReason.CHECK_REQUIRED,
            message="Run chkdsk /f and please try again, or see option -f.",
        )
        self.assertEqual(len(LANGUAGES), 28)
        for language in LANGUAGES:
            with self.subTest(language=language.code):
                namespace = {"_": _, "lang": language.code,
                             "NtfsResizeBlockReason": NtfsResizeBlockReason}
                exec(code, namespace)
                message = namespace["_resize_block_message"](inspection)
                self.assertTrue(message)
                self.assertNotIn("option -f", message)
                self.assertIn("chkdsk X: /f", message)
                self.assertNotIn("chkdsk C:", message)
                self.assertEqual(message, _(source, language.code))
                if language.code not in {DEFAULT_LANGUAGE, "en_GB"}:
                    self.assertNotEqual(message, source)
        self.assertEqual(_(source, "zh_CN"),
                         "此 NTFS 分区需要磁盘检查。请进入 Windows，备份重要文件，"
                         "以管理员身份运行“chkdsk X: /f”（将 X 替换为此分区的盘符）。"
                         "如提示安排检查，请确认，然后重启进入 Windows。"
                         "等待检查完成并彻底关闭 Windows 后，再重试。")

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

    def test_external_drive_mode_explanation_is_localized(self):
        source = (
            "External drive mode — AnduinOS will add a portable UEFI boot "
            "path so this drive can boot on another UEFI computer without "
            "an existing AnduinOS firmware boot entry."
        )
        for language in LANGUAGES:
            with self.subTest(language=language.code):
                translated = _(source, language.code)
                self.assertTrue(translated)
                if language.code not in {DEFAULT_LANGUAGE, "en_GB"}:
                    self.assertNotEqual(translated, source)

    def test_catalog_message_set_matches_source_and_policy(self):
        source_messages = set(KEYBOARD_LAYOUTS.values())
        for desktop in sorted((PACKAGE / "assets").glob("*.desktop")):
            for line in desktop.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key in {"Name", "GenericName", "Comment", "Keywords"}:
                    if value:
                        source_messages.add(value)
        for policy in sorted((PACKAGE / "assets").glob("*.policy")):
            root = ET.parse(policy).getroot()
            for action in root.findall("action"):
                for tag in ("description", "message"):
                    for element in action.findall(tag):
                        if "{http://www.w3.org/XML/1998/namespace}lang" not in element.attrib:
                            value = (element.text or "").strip()
                            if value:
                                source_messages.add(value)
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
