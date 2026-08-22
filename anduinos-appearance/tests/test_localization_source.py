import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUST_STRING = r'"(?:[^"\\]|\\.)*"'


class LocalizationSourceTests(unittest.TestCase):
    def test_formatted_messages_use_i18n_replace(self):
        violations = []
        pattern = re.compile(rf"\bi18n_replace\s*\(\s*({RUST_STRING})", re.DOTALL)
        for source in (ROOT / "src").glob("*.rs"):
            if source.name == "i18n.rs":
                continue
            content = source.read_text(encoding="utf-8")
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                if "i18n(" in match.group(0):
                    continue
                # i18n_replace's first argument must be a literal msgid.
                if match.group(1) is None:
                    violations.append(f"{source.relative_to(ROOT)}:{line}")
        self.assertEqual([], violations)

    def test_ui_text_is_not_a_raw_rust_literal(self):
        pattern = re.compile(
            rf"\.(?:label|title|subtitle|heading|body|description|"
            rf"placeholder_text|tooltip_text)\s*\(\s*&?({RUST_STRING})",
            re.DOTALL,
        )
        violations = []
        for source in (ROOT / "src").glob("*.rs"):
            content = source.read_text(encoding="utf-8")
            for match in pattern.finditer(content):
                if not any(character.isalpha() for character in match.group(1)):
                    continue
                line = content.count("\n", 0, match.start()) + 1
                violations.append(f"{source.relative_to(ROOT)}:{line}")
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
