import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScrollLayoutTests(unittest.TestCase):
    def test_every_explicit_scroller_uses_a_non_overlay_vertical_policy(self):
        source = (ROOT / "assets/anduinos-oobe").read_text(encoding="utf-8")

        self.assertEqual(source.count("scroll = Gtk.ScrolledWindow()"), 5)
        self.assertEqual(source.count("scroll.set_overlay_scrolling(False)"), 5)
        self.assertNotIn(
            "scroll.set_policy(Gtk.PolicyType.AUTOMATIC, "
            "Gtk.PolicyType.AUTOMATIC)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
