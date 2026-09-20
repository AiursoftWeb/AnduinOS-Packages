import tempfile
import unittest
from pathlib import Path

from keyboard_layouts import (
    is_valid_xkb_choice,
    keyboard_layouts,
    parse_xkb_rules,
    xkb_choice_id,
)
from languages import LANGUAGES


class KeyboardLayoutCatalogTests(unittest.TestCase):
    def test_system_catalog_exposes_all_layouts_and_variants(self):
        layouts = keyboard_layouts()
        by_id = {layout.id: layout for layout in layouts}

        self.assertGreaterEqual(len(layouts), 90)
        self.assertGreaterEqual(
            sum(len(layout.variants) for layout in layouts), 400
        )
        self.assertIn("hu", by_id)
        self.assertIn("qwerty", {item.id for item in by_id["hu"].variants})
        self.assertTrue(
            {"intl", "altgr-intl"}
            <= {item.id for item in by_id["us"].variants}
        )
        self.assertTrue(
            all(is_valid_xkb_choice(language.keyboard) for language in LANGUAGES)
        )

    def test_layout_and_variant_are_validated_as_one_catalogued_choice(self):
        self.assertTrue(is_valid_xkb_choice("hu"))
        self.assertTrue(is_valid_xkb_choice("hu", "qwerty"))
        self.assertTrue(is_valid_xkb_choice("us", "intl"))
        self.assertFalse(is_valid_xkb_choice("us", "qwerty"))
        self.assertFalse(is_valid_xkb_choice("missing"))

    def test_gnome_source_id_includes_a_selected_variant(self):
        self.assertEqual(xkb_choice_id("us"), "us")
        self.assertEqual(xkb_choice_id("us", "intl"), "us+intl")

    def test_parser_preserves_catalog_order_and_rejects_duplicates(self):
        document = """\
<xkbConfigRegistry><layoutList>
  <layout>
    <configItem><name>us</name><description>English</description></configItem>
    <variantList><variant><configItem>
      <name>intl</name><description>International</description>
    </configItem></variant></variantList>
  </layout>
  <layout>
    <configItem><name>hu</name><description>Hungarian</description></configItem>
  </layout>
</layoutList></xkbConfigRegistry>
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.xml"
            path.write_text(document, encoding="utf-8")
            parsed = parse_xkb_rules(path)
            path.write_text(
                document.replace("<name>hu</name>", "<name>us</name>"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "Duplicate XKB layout"):
                parse_xkb_rules(path)
        self.assertEqual([layout.id for layout in parsed], ["us", "hu"])
        self.assertEqual(parsed[0].variants[0].id, "intl")


if __name__ == "__main__":
    unittest.main()
