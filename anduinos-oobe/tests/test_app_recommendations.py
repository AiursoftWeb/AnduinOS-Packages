import ast
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "assets" / "anduinos-oobe"


def _translated_string(node):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_"
        and len(node.args) == 1
    ):
        node = node.args[0]
    return ast.literal_eval(node)


def _recommended_apps():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    create_apps_page = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_apps_page"
    )
    apps_assignment = next(
        node
        for node in create_apps_page.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "apps" for target in node.targets)
    )
    return [tuple(_translated_string(item) for item in entry.elts) for entry in apps_assignment.value.elts]


class AppRecommendationTests(unittest.TestCase):
    def test_nextcloud_is_recommended_from_flathub(self):
        self.assertIn(
            (
                "Nextcloud",
                "com.nextcloud.desktopclient.nextcloud",
                "Sync and collaborate on your desktop or laptop",
                "nextcloud.svg",
            ),
            _recommended_apps(),
        )

    def test_nextcloud_icon_is_bundled_and_valid(self):
        icon = ROOT / "resources" / "icons" / "nextcloud.svg"
        self.assertTrue(icon.is_file())
        self.assertEqual(ET.parse(icon).getroot().tag, "{http://www.w3.org/2000/svg}svg")


if __name__ == "__main__":
    unittest.main()
