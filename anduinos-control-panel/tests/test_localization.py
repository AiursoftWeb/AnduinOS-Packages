import ast
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
        for tag in ("description", "message"):
            locales = [
                node.attrib["{http://www.w3.org/XML/1998/namespace}lang"]
                for node in policy.findall(f".//{tag}")
                if "{http://www.w3.org/XML/1998/namespace}lang" in node.attrib
            ]
            self.assertEqual(len(locales), len(set(locales)), f"duplicate {tag}")
            self.assertEqual(set(locales), expected, tag)


if __name__ == "__main__":
    unittest.main()
