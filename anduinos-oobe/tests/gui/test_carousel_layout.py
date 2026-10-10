"""Exercise the actual wizard layout without starting setup or host probes."""

import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "assets" / "anduinos-oobe"
loader = importlib.machinery.SourceFileLoader("oobe_carousel_layout", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
oobe = importlib.util.module_from_spec(spec)
loader.exec_module(oobe)


class CarouselLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not oobe.Gtk.init_check() or oobe.Gdk.Display.get_default() is None:
            raise unittest.SkipTest("GTK display unavailable; run with xvfb-run")
        oobe.Adw.init()

    def settle(self):
        loop = oobe.GLib.MainLoop()
        oobe.GLib.timeout_add(100, lambda: (loop.quit(), False)[1])
        loop.run()

    def test_settled_pages_fill_viewport_and_hide_both_neighbors(self):
        settings = oobe.Gtk.Settings.get_default()
        animations = settings.get_property("gtk-enable-animations")
        settings.set_property("gtk-enable-animations", False)
        self.addCleanup(settings.set_property, "gtk-enable-animations", animations)
        # Only replace page content factories. Use the production window,
        # toolbar, clamps, scrollers, carousel and navigation callbacks.
        factories = [
            lambda: oobe.Gtk.Label(label="Previous page"),
            lambda: oobe.Gtk.Label(label="Current page"),
            lambda: oobe.create_finish_page(lambda: None),
        ]
        with patch.object(oobe.OobeWindow, "_get_page_factories", return_value=factories):
            for is_oobe in (False, True):
                for width in (745, 815, 1400):
                    with self.subTest(is_oobe=is_oobe, width=width):
                        window = oobe.OobeWindow(None, is_oobe)
                        try:
                            window.set_default_size(width, 992)
                            window.present()
                            self.settle()
                            carousel = window.carousel
                            # Include backwards navigation after the last page.
                            for selected in (0, 1, 2, 1, 0):
                                carousel.scroll_to(window._pages[selected], False)
                                self.settle()
                                self.assertAlmostEqual(carousel.get_position(), selected)
                                for index, page in enumerate(window._pages):
                                    valid, bounds = page.compute_bounds(carousel)
                                    self.assertTrue(valid)
                                    self.assertAlmostEqual(bounds.get_width(), carousel.get_width(), delta=1)
                                    if index == selected:
                                        self.assertAlmostEqual(bounds.get_x(), 0, delta=1)
                                    elif index < selected:
                                        self.assertLessEqual(bounds.get_x() + bounds.get_width(), 1)
                                    else:
                                        self.assertGreaterEqual(bounds.get_x(), carousel.get_width() - 1)
                        finally:
                            window.destroy()


if __name__ == "__main__":
    unittest.main()
