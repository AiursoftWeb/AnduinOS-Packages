import ast
import gettext
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent


def official_locale_names():
    policy = json.loads(
        (REPOSITORY / "anduinos-installer-beta/data/languages.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        language["locale"].split(".", 1)[0]
        for language in policy["languages"]
        if language["code"] != policy["default_language"]
    }


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {}
        with tempfile.TemporaryDirectory() as directory:
            for catalog in sorted((ROOT / "po").glob("*.po")):
                compiled = Path(directory) / f"{catalog.stem}.mo"
                subprocess.run(
                    [
                        "msgfmt", "--check", "--check-format",
                        "-o", str(compiled), str(catalog),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                with compiled.open("rb") as stream:
                    cls.catalogs[catalog.stem] = gettext.GNUTranslations(stream)

    def test_catalogs_cover_all_official_locales_and_messages(self):
        self.assertEqual(set(self.catalogs), official_locale_names() | {"en_US"})
        for locale in self.catalogs:
            with self.subTest(locale=locale):
                # English source fallback is intentional; all other catalogs
                # must contain nonempty, non-fuzzy entries for every message.
                options = ["--use-untranslated"] if locale == "en_US" else []
                subprocess.run(
                    [
                        "msgcmp", "--no-fuzzy-matching", *options,
                        str(ROOT / "po" / f"{locale}.po"),
                        str(ROOT / "po/anduinos-control-panel.pot"),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

    def test_mirror_terminology_is_consistent_throughout_each_workflow(self):
        # Match local terminology, allowing grammatical inflections. In Italian
        # "mirror" is the technical term; literal "specchio" is inappropriate.
        terms = {
            "ar_SA": r"خ(?:ادم|وادم) (?:ال)?مرآة",
            "da_DK": r"spejlserver",
            "de_DE": r"Spiegelserver",
            "el_GR": r"διακομιστ\w+ λήψης",
            "en_GB": r"mirror",
            "en_US": r"mirror",
            "es_ES": r"espejo",
            "fi_FI": r"peilipalveli",
            "fr_FR": r"miroir",
            "hi_IN": r"मिरर",
            "id_ID": r"server cermin",
            "it_IT": r"mirror",
            "ja_JP": r"ミラー",
            "ko_KR": r"미러",
            "nl_NL": r"spiegelserver",
            "pl_PL": r"serwer\w* lustrzan",
            "pt_BR": r"espelho",
            "pt_PT": r"espelho",
            "ro_RO": r"server\w* oglindă",
            "ru_RU": r"зеркал",
            "sv_SE": r"spegl|spegel",
            "th_TH": r"มิเรอร์",
            "tr_TR": r"yansı",
            "uk_UA": r"дзеркал",
            "vi_VN": r"máy chủ phản chiếu",
            "zh_CN": r"镜像源",
            "zh_HK": r"鏡像站",
            "zh_TW": r"鏡像站",
        }
        self.assertEqual(set(terms), set(self.catalogs))
        source = ROOT / "src/anduinos_control_panel/software_sources.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        messages = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and "mirror" in node.args[0].value.lower()
        }
        self.assertGreaterEqual(len(messages), 10)
        for locale, translations in self.catalogs.items():
            pattern = re.compile(terms[locale], re.IGNORECASE)
            for message in messages:
                with self.subTest(locale=locale, message=message):
                    translated = translations.gettext(message)
                    self.assertRegex(translated, pattern)
                    if not locale.startswith("en_"):
                        self.assertNotEqual(translated, message)
                    if "apt update" in message:
                        self.assertIn("apt update", translated)

    def test_factory_reset_is_localized_in_every_non_english_catalog(self):
        messages = (
            "Factory Reset",
            "Return system files and applications to their initial state",
            "Factory Reset Is Not Available",
            "This system does not support factory reset. Reinstall AnduinOS "
            "and choose the Btrfs filesystem to enable it.",
        )
        for locale, translations in self.catalogs.items():
            if not locale.startswith("en_"):
                for message in messages:
                    self.assertNotEqual(
                        translations.gettext(message),
                        message,
                        f"{locale}: {message}",
                    )

    def test_boot_resolution_warning_is_localized_in_all_28_catalogs(self):
        message = (
            "High resolution can make menu text small on 4K displays; "
            "the screen's native resolution is not guaranteed at boot."
        )
        self.assertEqual(len(self.catalogs), 28)
        for locale, translations in self.catalogs.items():
            with self.subTest(locale=locale):
                translated = translations.gettext(message)
                self.assertIn("4K", translated)
                if not locale.startswith("en_"):
                    self.assertNotEqual(translated, message)

    def test_template_is_reproducible_from_python_and_desktop_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            extracted = Path(directory) / "messages.pot"
            subprocess.run(
                [
                    "xgettext",
                    "--language=Python",
                    "--keyword=_",
                    "--keyword=N_",
                    "--from-code=UTF-8",
                    "--no-wrap",
                    f"--output={extracted}",
                    *map(str, sorted((ROOT / "src/anduinos_control_panel").glob("*.py"))),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "xgettext",
                    "--join-existing",
                    "--language=Desktop",
                    "--from-code=UTF-8",
                    "--no-wrap",
                    f"--output={extracted}",
                    str(ROOT / "data/com.anduinos.ControlPanel.desktop"),
                ],
                check=True,
            )
            template = ROOT / "po/anduinos-control-panel.pot"
            for definition, reference in ((template, extracted), (extracted, template)):
                subprocess.run(
                    [
                        "msgcmp",
                        "--use-untranslated",
                        "--no-fuzzy-matching",
                        str(definition),
                        str(reference),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

    def test_topic_titles_and_descriptions_are_extractable(self):
        tree = ast.parse(
            (ROOT / "src/anduinos_control_panel/topics.py").read_text(
                encoding="utf-8"
            )
        )
        topic_messages = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "N_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        template = (ROOT / "po/anduinos-control-panel.pot").read_text(
            encoding="utf-8"
        )
        self.assertTrue(topic_messages)
        for message in topic_messages:
            self.assertIn(f"msgid {json.dumps(message)}", template)

    def test_desktop_and_polkit_assets_cover_every_non_source_locale(self):
        expected = official_locale_names()
        desktop = (ROOT / "data/com.anduinos.ControlPanel.desktop").read_text(
            encoding="utf-8"
        )
        for key in ("Name", "Comment", "Keywords"):
            found = set(re.findall(rf"^{key}\[([^]]+)\]=", desktop, re.MULTILINE))
            self.assertEqual(found, expected, key)

        policy = ET.parse(ROOT / "data/com.anduinos.ControlPanel.policy")
        for action in policy.findall("action"):
            for tag in ("description", "message"):
                locales = [
                    node.attrib["{http://www.w3.org/XML/1998/namespace}lang"]
                    for node in action.findall(tag)
                    if "{http://www.w3.org/XML/1998/namespace}lang" in node.attrib
                ]
                label = f"{action.attrib['id']} {tag}"
                self.assertEqual(len(locales), len(set(locales)), label)
                self.assertEqual(set(locales), expected, label)


if __name__ == "__main__":
    unittest.main()
