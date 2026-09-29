"""Keep software choices and navigation usable on low-resolution displays."""

import time
import unittest
from unittest.mock import patch

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


class SoftwarePageLayoutTests(unittest.TestCase):
    def test_keyboard_dropdown_search_filters_layouts_and_variants(self):
        page = pages.build_keyboard_page({"lang": "en_US"}, Adw.NavigationView())
        dropdowns = [w for w in _descendants(page)
                     if isinstance(w, Gtk.DropDown)]
        self.assertEqual(len(dropdowns), 2)
        layout, variant = dropdowns

        for dropdown, query in (
            (layout, "Chines"),
            (variant, "Macintosh"),
        ):
            self.assertTrue(dropdown.get_enable_search())
            expression = dropdown.get_expression()
            self.assertIsNotNone(expression)
            search_filter = Gtk.StringFilter.new(expression)
            search_filter.set_search(query)
            model = dropdown.get_model()
            matches = [model.get_item(i).get_string()
                       for i in range(model.get_n_items())
                       if search_filter.match(model.get_item(i))]
            self.assertTrue(matches)
            self.assertLess(len(matches), model.get_n_items())
            self.assertTrue(all(query.casefold() in item.casefold()
                                for item in matches))

    def test_keyboard_page_uses_the_same_compact_layout(self):
        window = Adw.Window(default_width=800, default_height=600)
        self.addCleanup(window.destroy)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        navigation = Adw.NavigationView()
        page = pages.build_keyboard_page({"lang": "en_US"}, navigation)
        navigation.add(page)
        toolbar.set_content(navigation)
        window.set_content(toolbar)
        window.present()
        _settle()

        widgets = list(_descendants(page))
        hero = next(w for w in widgets if w.has_css_class("installer-hero"))
        footer = next(w for w in widgets if w.has_css_class("wizard-navigation"))
        self.assertFalse(hero.get_visible())
        self.assertTrue(footer.get_mapped())
        self.assertTrue(any(isinstance(w, Gtk.ScrolledWindow)
                            and w.get_vscrollbar().get_mapped() for w in widgets))

    def test_800x600_keeps_navigation_visible_and_options_scrollable(self):
        window = Adw.Window(default_width=800, default_height=600)
        self.addCleanup(window.destroy)
        load_visual_style(window.get_display())
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        navigation = Adw.NavigationView()
        page = pages.build_software_page({"lang": "en_US"}, navigation)
        navigation.add(page)
        toolbar.set_content(navigation)
        window.set_content(toolbar)
        window.present()
        _settle()

        widgets = list(_descendants(page))
        hero = next(w for w in widgets if w.has_css_class("installer-hero"))
        scroll = next(w for w in widgets if isinstance(w, Gtk.ScrolledWindow))
        footer = next(w for w in widgets if w.has_css_class("wizard-navigation"))
        continue_button = next(w for w in widgets
                               if isinstance(w, Gtk.Button) and w.get_label() == "Next")

        self.assertFalse(hero.get_visible())
        self.assertTrue(scroll.get_vscrollbar().get_mapped())
        self.assertTrue(footer.get_mapped())
        self.assertTrue(continue_button.get_mapped())
        ok, bounds = continue_button.compute_bounds(window)
        self.assertTrue(ok)
        self.assertLessEqual(bounds.get_y() + bounds.get_height(), window.get_height())

        choices = scroll.get_child().get_child()
        for _ in range(12):
            choices.append(Gtk.Label(label="Additional software choice"))
        _settle()
        adjustment = scroll.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        _settle()
        self.assertGreater(adjustment.get_value(), 0)
        self.assertTrue(continue_button.get_mapped())

        next_page = Adw.NavigationPage(title="Next page")
        with patch("pages.build_disk_page", return_value=next_page):
            continue_button.emit("clicked")
        _settle()
        self.assertIs(navigation.get_visible_page(), next_page)

    def test_normal_height_keeps_full_header(self):
        window = Adw.Window(default_width=960, default_height=680)
        self.addCleanup(window.destroy)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        navigation = Adw.NavigationView()
        page = pages.build_software_page({"lang": "en_US"}, navigation)
        navigation.add(page)
        toolbar.set_content(navigation)
        window.set_content(toolbar)
        window.present()
        _settle()

        widgets = list(_descendants(page))
        hero = next(w for w in widgets if w.has_css_class("installer-hero"))
        self.assertTrue(hero.get_visible())
