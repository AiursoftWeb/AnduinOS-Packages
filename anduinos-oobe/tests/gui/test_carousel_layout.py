"""Exercise the actual wizard layout without starting setup or host probes."""

import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch, Mock


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

    def test_source_cards_apply_on_click_and_never_require_an_extra_next(self):
        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()
        for warnings in ([], ["unreachable"]):
            with self.subTest(warnings=warnings):
                navigate, update = Mock(), Mock()
                page = oobe.create_flathub_mirror_page(navigate, update)
                buttons = [w for w in descendants(page) if w.has_css_class("oobe-source-choice")]
                self.assertEqual(len(buttons), 2)
                with patch.object(oobe._software_sources, "switch_to_china", return_value=warnings) as switch, patch.object(oobe.threading, "Thread") as thread:
                    switch.assert_not_called()
                    buttons[0].emit("clicked")
                    navigate.assert_called_once_with()
                    navigate.reset_mock()
                    buttons[1].emit("clicked")
                    self.assertTrue(page._install_running)
                    self.assertTrue(page._block_carousel)
                    self.assertTrue(all(not button.get_sensitive() for button in buttons))
                    buttons[1].emit("clicked")
                    self.assertEqual(thread.call_count, 1)
                    thread.call_args.kwargs["target"]()
                    self.settle()
                    switch.assert_called_once_with()
                    self.assertFalse(page._install_running)
                    self.assertTrue(all(button.get_sensitive() for button in buttons))
                    if warnings:
                        navigate.assert_not_called()
                        self.assertFalse(page._hide_next)
                    else:
                        navigate.assert_called_once_with()

    def test_driver_page_can_continue_before_and_after_opening_driver_center(self):
        settings = oobe.Gtk.Settings.get_default()
        animations = settings.get_property("gtk-enable-animations")
        settings.set_property("gtk-enable-animations", False)
        self.addCleanup(settings.set_property, "gtk-enable-animations", animations)

        def descendants(widget):
            yield widget
            child = widget.get_first_child()
            while child:
                yield from descendants(child)
                child = child.get_next_sibling()

        for is_oobe in (False, True):
            with self.subTest(is_oobe=is_oobe):
                window = None
                factories = [
                    lambda: oobe.Gtk.Label(label="Security"),
                    lambda: oobe.create_hardware_drivers_page(lambda: window._nav_next()),
                    lambda: oobe.Gtk.Label(label="Applications"),
                ]
                with patch.object(oobe.OobeWindow, "_get_page_factories", return_value=factories):
                    window = oobe.OobeWindow(None, is_oobe)
                try:
                    window.present()
                    window.carousel.scroll_to(window._pages[1], False)
                    self.settle()
                    self.assertTrue(window.next_btn.get_visible())
                    self.assertTrue(window.next_btn.get_sensitive())
                    window.next_btn.emit("clicked")
                    self.settle()
                    self.assertAlmostEqual(window.carousel.get_position(), 2)

                    window.back_btn.emit("clicked")
                    self.settle()
                    page = window._content_page(window._pages[1])
                    buttons = {item.get_label(): item for item in descendants(page)
                               if isinstance(item, oobe.Gtk.Button)}
                    with patch.object(oobe.subprocess, "Popen") as launch:
                        buttons["Open Driver Center"].emit("clicked")
                        self.settle()
                        launch.assert_called_once_with([oobe.DRIVER_CENTER_EXECUTABLE])
                        self.assertAlmostEqual(window.carousel.get_position(), 1)
                        self.assertTrue(window.next_btn.get_visible())
                        window.next_btn.emit("clicked")
                        self.settle()
                        self.assertAlmostEqual(window.carousel.get_position(), 2)
                        launch.assert_called_once()

                    window.back_btn.emit("clicked")
                    self.settle()
                    buttons["Skip"].emit("clicked")
                    self.settle()
                    self.assertAlmostEqual(window.carousel.get_position(), 2)
                finally:
                    window.destroy()

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
