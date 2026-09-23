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
        authorization_defaults = {
            annotations[index]["org.freedesktop.policykit.exec.path"]: tuple(
                (actions[index].findtext(f"./defaults/{state}") or "").strip()
                for state in ("allow_any", "allow_inactive", "allow_active")
            )
            for index in range(len(actions))
        }
        self.assertEqual(
            authorization_defaults["/usr/libexec/anduinos-rescue-center-helper"],
            ("no", "no", "auth_admin_keep"),
        )
        self.assertEqual(
            authorization_defaults[
                "/usr/libexec/anduinos-rescue-center-live-helper"
            ],
            ("yes", "yes", "yes"),
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

    def test_live_session_creates_a_trusted_desktop_shortcut(self):
        creator = (
            ROOT / "scripts/anduinos-rescue-center-live-shortcut"
        ).read_text(encoding="utf-8")
        self.assertIn('[ -d /cdrom ] || exit 0', creator)
        self.assertIn(
            "test -f /run/anduinos-live/environment || exit 0", creator
        )
        self.assertIn(
            "source=/usr/share/applications/com.anduinos.RescueCenter.desktop",
            creator,
        )
        self.assertIn('install -m 0755 "$source" "$destination"', creator)
        self.assertIn("metadata::trusted true", creator)
        self.assertNotIn("exec /usr/bin/anduinos-rescue-center", creator)

        autostart = (
            ROOT / "data/anduinos-rescue-center-live-shortcut.desktop"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Exec=/usr/lib/anduinos-rescue-center/create-live-shortcut\n",
            autostart,
        )
        self.assertIn("OnlyShowIn=GNOME;\n", autostart)
        self.assertIn("NoDisplay=true\n", autostart)

        project = (ROOT / "anduinos-rescue-center.aosproj").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'Target="/usr/lib/anduinos-rescue-center/create-live-shortcut"',
            project,
        )
        self.assertIn(
            'Target="/etc/xdg/autostart/anduinos-rescue-center-live-shortcut.desktop"',
            project,
        )

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

    def test_quick_installations_use_an_activatable_preferences_group(self):
        source = (ROOT / "src/anduinos_rescue_center/app.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        window = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RescueWindow"
        )
        render = next(
            node for node in window.body
            if isinstance(node, ast.FunctionDef) and node.name == "_render"
        )
        installation_rows = [
            call for call in ast.walk(render)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "add"
            and call.args
            and isinstance(call.args[0], ast.Call)
            and isinstance(call.args[0].func, ast.Attribute)
            and call.args[0].func.attr == "_installation_row"
        ]
        self.assertEqual(len(installation_rows), 1)

        row_builder = next(
            node for node in window.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_installation_row"
        )
        activation_handlers = [
            node for node in ast.walk(row_builder)
            if isinstance(node, ast.Lambda) and node.args.defaults
        ]
        self.assertEqual(len(activation_handlers), 1)

    def test_invalid_target_returns_to_selection_with_a_dialog(self):
        source = (ROOT / "src/anduinos_rescue_center/app.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        window = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RescueWindow"
        )
        methods = {
            node.name: node
            for node in window.body
            if isinstance(node, ast.FunctionDef)
        }
        open_system = methods["_open_system"]
        self.assertTrue(any(
            isinstance(node, ast.Attribute) and node.attr == "_open_failed"
            for node in ast.walk(open_system)
        ))

        open_failed = methods["_open_failed"]
        calls = [node for node in ast.walk(open_failed) if isinstance(node, ast.Call)]
        self.assertTrue(any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "MessageDialog"
            for call in calls
        ))
        self.assertTrue(any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_visible_child_name"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == "content"
            for call in calls
        ))
        self.assertTrue(any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "present"
            for call in calls
        ))

    def test_main_content_scrolls_instead_of_forcing_window_height(self):
        source = (ROOT / "src/anduinos_rescue_center/app.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        window = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "RescueWindow"
        )
        constructor = next(
            node for node in window.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        calls = [node for node in ast.walk(constructor) if isinstance(node, ast.Call)]
        self.assertTrue(any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_default_size"
            and [arg.value for arg in call.args if isinstance(arg, ast.Constant)]
            == [900, 640]
            for call in calls
        ))
        self.assertTrue(any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "ScrolledWindow"
            for call in calls
        ))
        self.assertTrue(any(
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_child"
            and call.args
            and isinstance(call.args[0], ast.Attribute)
            and call.args[0].attr == "content"
            for call in calls
        ))
