import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUST_STRING = r'"(?:[^"\\]|\\.)*"'


class LocalizationSourceTests(unittest.TestCase):
    def test_desktop_entry_is_exposed_as_a_control_panel_module(self):
        desktop = (ROOT / "data" / "com.anduinos.ufwall.desktop").read_text(
            encoding="utf-8"
        )
        self.assertIn("\nNoDisplay=true\n", desktop)
        self.assertIn("\nExec=ufwall-gtk\n", desktop)
        self.assertIn("\nIcon=com.anduinos.ufwall\n", desktop)

    def test_window_opens_at_the_taller_dashboard_size(self):
        source = (ROOT / "src" / "window.rs").read_text(encoding="utf-8")
        self.assertIn('.property("default-width", 1070)', source)
        self.assertIn('.property("default-height", 770)', source)
        self.assertNotIn('.property("default-width", 1100)', source)
        self.assertNotIn('.property("default-height", 650)', source)

    def test_ui_text_is_not_a_raw_rust_literal(self):
        pattern = re.compile(
            rf"\.(?:label|title|subtitle|heading|body|description|"
            rf"placeholder_text|tooltip_text)\s*\(\s*&?({RUST_STRING})",
            re.DOTALL,
        )
        violations = []
        for source in (ROOT / "src").rglob("*.rs"):
            content = source.read_text(encoding="utf-8")
            for match in pattern.finditer(content):
                if match.group(1) == '"0 B/s"':
                    continue
                if not any(character.isalpha() for character in match.group(1)):
                    continue
                line = content.count("\n", 0, match.start()) + 1
                violations.append(f"{source.relative_to(ROOT)}:{line}")
        self.assertEqual([], violations)

    def test_user_facing_error_prefixes_use_i18n(self):
        forbidden = re.compile(
            r'(?:message:\s*format!|Err\(format!)\(\s*"(?:Failed|Cannot|Authentication|No ports)'
        )
        violations = []
        for source in (ROOT / "src" / "ufw").rglob("*.rs"):
            content = source.read_text(encoding="utf-8")
            for match in forbidden.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                violations.append(f"{source.relative_to(ROOT)}:{line}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
