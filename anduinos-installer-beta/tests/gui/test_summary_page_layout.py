"""Long disk identities must not push installation controls off screen."""

import time
import unittest

import pages
from gi.repository import Adw, GLib, Gtk

from ui import load_visual_style


def _descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _descendants(child)
        child = child.get_next_sibling()


def _settle():
    deadline = time.monotonic() + 0.35
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


class SummaryPageLayoutTests(unittest.TestCase):
    def test_long_disk_identity_keeps_navigation_visible_and_summary_scrollable(self):
        stable_id = "wwn:nvme." + "144d5336374b4e453057313030333830" * 6
        for lang in ("en_US", "zh_CN"):
            for width, height in ((800, 600), (960, 720)):
                with self.subTest(lang=lang, size=(width, height)):
                    window = Adw.Window(default_width=width, default_height=height)
                    try:
                        load_visual_style(window.get_display())
                        toolbar = Adw.ToolbarView()
                        toolbar.add_top_bar(Adw.HeaderBar())
                        navigation = Adw.NavigationView()
                        page = pages.build_summary_page({
                            "lang": lang,
                            "disk": "/dev/nvme0n1",
                            "disk_size": "953.9 GiB",
                            "disk_model": "SAMSUNG MZAL41T0HBLB-00BL2",
                            "disk_stable_id": stable_id,
                        }, navigation)
                        navigation.add(page)
                        toolbar.set_content(navigation)
                        window.set_content(toolbar)
                        window.present()
                        _settle()

                        widgets = list(_descendants(page))
                        summary = next(w for w in widgets
                                       if isinstance(w, Gtk.Label)
                                       and stable_id in w.get_text())
                        footer = next(w for w in widgets
                                      if w.has_css_class("wizard-navigation"))
                        scroll = next(w for w in widgets
                                      if isinstance(w, Gtk.ScrolledWindow)
                                      and summary in _descendants(w))
                        adjustment = scroll.get_vadjustment()
                        self.assertGreater(adjustment.get_upper(),
                                           adjustment.get_page_size())
                        for position in (0, adjustment.get_upper()
                                         - adjustment.get_page_size()):
                            adjustment.set_value(position)
                            _settle()
                            for button in (footer.back_button, footer.next_button):
                                self.assertTrue(button.get_mapped())
                                ok, bounds = button.compute_bounds(window)
                                self.assertTrue(ok)
                                self.assertGreaterEqual(bounds.get_x(), 0)
                                self.assertGreaterEqual(bounds.get_y(), 0)
                                self.assertLessEqual(bounds.get_x() + bounds.get_width(),
                                                     window.get_width())
                                self.assertLessEqual(bounds.get_y() + bounds.get_height(),
                                                     window.get_height())
                    finally:
                        window.destroy()
