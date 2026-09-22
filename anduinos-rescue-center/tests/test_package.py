import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackageContractTests(unittest.TestCase):
    def test_source_tree_contains_no_python_bytecode(self):
        generated = []
        for source in (ROOT / "src", ROOT / "scripts"):
            generated.extend(
                path
                for path in source.rglob("*")
                if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}
            )
        self.assertEqual(generated, [])

    def test_polkit_authorizes_only_the_fixed_helper(self):
        policy = ET.parse(ROOT / "data/com.anduinos.RescueCenter.policy")
        actions = policy.findall(".//action")
        self.assertEqual(len(actions), 2)
        annotations = [{
            item.attrib.get("key"): (item.text or "").strip()
            for item in action.findall("annotate")
        } for action in actions]
        self.assertEqual(
            {
                item["org.freedesktop.policykit.exec.path"] for item in annotations
            },
            {
                "/usr/libexec/anduinos-rescue-center-helper",
                "/usr/libexec/anduinos-rescue-center-live-helper",
            },
        )
        active_defaults = {
            annotations[index]["org.freedesktop.policykit.exec.path"]:
                (actions[index].findtext("./defaults/allow_active") or "").strip()
            for index in range(len(actions))
        }
        self.assertEqual(
            active_defaults["/usr/libexec/anduinos-rescue-center-helper"],
            "auth_admin_keep",
        )
        self.assertEqual(
            active_defaults["/usr/libexec/anduinos-rescue-center-live-helper"],
            "yes",
        )
        live_helper = (ROOT / "scripts/anduinos-rescue-center-live-helper").read_text(
            encoding="utf-8"
        )
        self.assertIn("if not is_live_environment():", live_helper)

    def test_desktop_launcher_is_unprivileged(self):
        desktop = (ROOT / "data/com.anduinos.RescueCenter.desktop").read_text(
            encoding="utf-8"
        )
        self.assertIn("Exec=anduinos-rescue-center\n", desktop)
        self.assertNotIn("pkexec", desktop)

    def test_ui_instance_fields_do_not_shadow_methods(self):
        source = (ROOT / "src/anduinos_rescue_center/app.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        for node in (item for item in tree.body if isinstance(item, ast.ClassDef)):
            methods = {
                item.name for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            fields = {
                item.attr for item in ast.walk(node)
                if isinstance(item, ast.Attribute)
                and isinstance(item.ctx, ast.Store)
                and isinstance(item.value, ast.Name)
                and item.value.id == "self"
            }
            self.assertFalse(methods & fields, f"{node.name}: {methods & fields}")
