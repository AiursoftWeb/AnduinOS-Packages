"""Selected rows must stay inside rounded list cards, even while scrolling."""

import time
import unittest

import pages
from gi.repository import Adw, GLib, Gtk

from ui import load_visual_style


def _settle():
    deadline = time.monotonic() + 0.35
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


def _descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _descendants(child)
        child = child.get_next_sibling()


class ListCardClippingTests(unittest.TestCase):
    def _show_page(self, builder, shared):
        window = Adw.Window(default_width=960, default_height=720)
        self.addCleanup(window.destroy)
        load_visual_style(window.get_display())
        navigation = Adw.NavigationView()
        page = builder(shared, navigation)
        navigation.add(page)
        window.set_content(navigation)
        window.present()
        _settle()
        card = next(w for w in _descendants(page)
                    if w.has_css_class("installer-list-card"))
        return window, page, card

    def _assert_rounded_clip(self, card):
        self.assertEqual(card.get_overflow(), Gtk.Overflow.HIDDEN)
        width, height = card.get_width(), card.get_height()
        self.assertGreater(width, 32)
        self.assertGreater(height, 32)
        # GTK uses the same rounded clip for drawing and picking. The four
        # corners must not target a row or scrollbar outside the card's curve.
        for x, y in ((2, 2), (width - 3, 2),
                     (2, height - 3), (width - 3, height - 3)):
            with self.subTest(corner=(x, y)):
                self.assertIsNone(card.pick(x, y, Gtk.PickFlags.DEFAULT))
        self.assertIsNotNone(card.pick(24, 24, Gtk.PickFlags.DEFAULT))

    def test_timezone_card_clips_selection_in_both_themes_and_after_filtering(self):
        manager = Adw.StyleManager.get_default()
        original_scheme = manager.get_color_scheme()
        self.addCleanup(manager.set_color_scheme, original_scheme)
        for scheme in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
            with self.subTest(scheme=scheme):
                manager.set_color_scheme(scheme)
                shared = {"lang": "en_US", "timezone": "UTC"}
                window, page, card = self._show_page(
                    pages.build_timezone_page, shared)
                self._assert_rounded_clip(card)
                selection = card.get_child().get_model()
                self.assertEqual(selection.get_selected_item().get_string(), "UTC")

                adjustment = card.get_vadjustment()
                self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
                adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
                _settle()
                self.assertGreater(adjustment.get_value(), 0)
                self._assert_rounded_clip(card)

                search = next(w for w in _descendants(page)
                              if isinstance(w, Gtk.SearchEntry))
                search.set_text("Tokyo")
                search.emit("search-changed")
                _settle()
                self.assertEqual(selection.get_model().get_n_items(), 1)
                self.assertEqual(shared["timezone"], "UTC")
                selection.select_item(0, True)
                _settle()
                self.assertEqual(shared["timezone"], "Asia/Tokyo")
                self._assert_rounded_clip(card)
                window.destroy()

    def test_welcome_language_card_uses_the_same_rounded_clip(self):
        _, _, card = self._show_page(
            pages.build_welcome_page, {"lang": "en_US"})
        self._assert_rounded_clip(card)
