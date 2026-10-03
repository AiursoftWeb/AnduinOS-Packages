import unittest
from pathlib import Path

from languages import LANGUAGES
from slideshow import SLIDE_ORDER, load_slides


class SlideshowAssetsTests(unittest.TestCase):
    def test_every_supported_language_loads_seven_native_slides(self):
        root = Path(__file__).resolve().parent.parent / "assets/slideshow"
        for language in LANGUAGES:
            slides = load_slides(language.code, root)
            self.assertEqual(
                tuple(slide.key for slide in slides), SLIDE_ORDER
            )
            self.assertTrue(all(slide.title for slide in slides))
            self.assertTrue(all(slide.body for slide in slides))
            self.assertTrue(all(slide.image.is_file() for slide in slides))

    def test_unknown_language_falls_back_to_english(self):
        root = Path(__file__).resolve().parent.parent / "assets/slideshow"
        slides = load_slides("unknown", root)
        self.assertEqual(slides[0].title, "Welcome to AnduinOS")

    def test_historical_emphasis_is_preserved_as_safe_pango_markup(self):
        slides = load_slides("en_US")
        privacy = next(slide for slide in slides if slide.key == "privacy")
        self.assertIn("<b>your device serves your intent", privacy.paragraphs_markup[1])
        self.assertNotIn("<a", slides[0].paragraphs_markup[0])
        self.assertTrue((privacy.image.parent.parent / "background.png").is_file())
