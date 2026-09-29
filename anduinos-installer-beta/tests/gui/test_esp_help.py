"""Exercise the real help window, all translations, and read-only page wiring."""

import unittest
import time
from unittest.mock import Mock, patch

import pages
from gi.repository import Gdk, GLib, Gtk
from i18n import _
from languages import LANGUAGES
from test_esp_storage_page import descendants
from test_frontend import state


def dispatch_refresh():
    # A mapped window may continuously enqueue animation frames. Bound the
    # outer wait rather than draining the context until it becomes empty.
    deadline = time.monotonic() + 0.15
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)


class EspHelpTests(unittest.TestCase):
    def test_all_languages_show_left_right_comparison_without_text_overflow(self):
        parent = Gtk.Window()
        self.addCleanup(parent.destroy)
        parent.present()
        for language in LANGUAGES:
            with self.subTest(language=language.code):
                window = pages._esp_help_dialog(parent, language.code)
                try:
                    dispatch_refresh()
                    self.assertTrue(window.get_modal())
                    self.assertIs(parent, window.get_transient_for())
                    labels = [w for w in descendants(window) if isinstance(w, Gtk.Label)]
                    texts = [w.get_text() for w in labels]
                    for message in ("Separate ESP", "Shared ESP", pages._ESP_HELP_SEPARATE,
                                    pages._ESP_HELP_SHARED, pages._ESP_HELP_COMMON):
                        self.assertIn(_(message, language.code), texts)
                    left = next(w for w in labels if w.get_text() == _("Separate ESP", language.code))
                    right = next(w for w in labels if w.get_text() == _("Shared ESP", language.code))
                    left_column, right_column = left.get_parent(), right.get_parent()
                    self.assertIs(left_column.get_parent(), right_column.get_parent())
                    self.assertLess(left_column.get_allocation().x, right_column.get_allocation().x)
                    for widget in labels:
                        if widget.get_wrap():
                            self.assertLessEqual(widget.get_layout().get_pixel_size()[0], widget.get_width() + 1)
                finally:
                    window.destroy()

    def test_question_button_opens_modal_help_and_ok_closes_it(self):
        parent = Gtk.Window()
        self.addCleanup(parent.destroy)
        button = pages._esp_help_button("zh_CN")
        parent.set_child(button)
        parent.present()
        dispatch_refresh()
        self.assertEqual("dialog-question-symbolic", button.get_icon_name())
        self.assertEqual(_("Compare ESP options", "zh_CN"), button.get_tooltip_text())
        with patch("pages._esp_help_dialog", wraps=pages._esp_help_dialog) as opened:
            button.emit("clicked")
            opened.assert_called_once_with(parent, "zh_CN")
        dialog = next(w for w in Gtk.Window.list_toplevels() if w.get_transient_for() is parent)
        try:
            dispatch_refresh()
            ok = next(w for w in descendants(dialog)
                      if isinstance(w, Gtk.Button) and w.get_label() == _("OK", "zh_CN"))
            self.assertIs(ok, dialog.get_focus())
            ok.emit("clicked")
            dispatch_refresh()
            self.assertFalse(dialog.get_visible())
        finally:
            dialog.destroy()

    def test_escape_closes_help(self):
        parent = Gtk.Window()
        self.addCleanup(parent.destroy)
        window = pages._esp_help_dialog(parent, "en_US")
        self.addCleanup(window.destroy)
        controllers = window.observe_controllers()
        key = next(controllers.get_item(index) for index in range(controllers.get_n_items())
                   if isinstance(controllers.get_item(index), Gtk.EventControllerKey))
        self.assertTrue(key.emit("key-pressed", Gdk.KEY_Escape, 0, Gdk.ModifierType(0)))
        dispatch_refresh()
        self.assertFalse(window.get_visible())

    def test_both_storage_pages_have_help_without_changing_state(self):
        for builder in (pages.build_advanced_storage_page, pages.build_guided_storage_page):
            with self.subTest(builder=builder.__name__), patch("pages.LatestBackgroundRequest"):
                shared = state()
                page = builder(shared, Mock())
                before = shared.copy()
                buttons = [w for w in descendants(page)
                           if isinstance(w, Gtk.Button)
                           and w.get_tooltip_text() == _("Compare ESP options", shared["lang"])]
                self.assertEqual(1, len(buttons))
                self.assertIsInstance(buttons[0].get_prev_sibling(), Gtk.DropDown)
                with patch("pages._esp_help_dialog") as help_dialog:
                    buttons[0].emit("clicked")
                help_dialog.assert_called_once()
                self.assertEqual(before, shared)
