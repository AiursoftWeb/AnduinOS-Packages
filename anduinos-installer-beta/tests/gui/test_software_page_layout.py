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
    def test_only_simplified_chinese_routes_through_source_choice(self):
        from unittest.mock import Mock
        for lang in ("zh_CN", "zh_TW", "en_US"):
            with self.subTest(lang=lang):
                shared = {"lang": lang, "download_from_china": True,
                          "_page_route_initialized": True}
                navigation = Mock()
                chosen = Adw.NavigationPage(title="Source choice")
                summary = Adw.NavigationPage(title="Review")
                page = pages.build_timezone_page(shared, navigation)
                footer = next(w for w in _descendants(page) if w.has_css_class("wizard-navigation"))
                with patch("pages.build_china_sources_page", return_value=chosen), patch("pages.build_summary_page", return_value=summary):
                    footer.next_button.emit("clicked")
                navigation.push.assert_called_once_with(chosen if lang == "zh_CN" else summary)
                if lang != "zh_CN":
                    self.assertFalse(shared["download_from_china"])

    def test_china_choice_only_records_plan_preference(self):
        from unittest.mock import Mock
        for selected in (False, True):
            shared = {"lang": "zh_CN", "download_from_china": not selected,
                      "_page_route_initialized": True}
            navigation = Mock()
            summary = Adw.NavigationPage(title="Review")
            with patch("pages.subprocess.run") as command, patch("pages.build_summary_page", return_value=summary):
                page = pages.build_china_sources_page(shared, navigation)
                buttons = [w for w in _descendants(page)
                           if isinstance(w, Gtk.Button) and w.has_css_class("china-source-choice")]
                self.assertEqual(len(buttons), 2)
                self.assertIn("AIURSOFT LIMITED", buttons[0].get_tooltip_text())
                self.assertIn("苏州艾软科技", buttons[1].get_tooltip_text())
                buttons[int(selected)].emit("clicked")
                command.assert_not_called()
            self.assertIs(shared["download_from_china"], selected)
            navigation.push.assert_called_once_with(summary)

    def test_china_choices_fit_low_resolution_and_preserve_back_navigation(self):
        window = Adw.Window(default_width=800, default_height=600)
        self.addCleanup(window.destroy)
        navigation = Adw.NavigationView()
        shared = {"lang": "zh_CN", "_page_route_initialized": True}
        page = pages.build_china_sources_page(shared, navigation)
        navigation.add(page)
        window.set_content(navigation)
        window.present()
        _settle()
        widgets = list(_descendants(page))
        buttons = [w for w in widgets if w.has_css_class("china-source-choice")]
        self.assertEqual(len(buttons), 2)
        self.assertTrue(all(w.get_mapped() for w in buttons))
        footer = next(w for w in widgets if w.has_css_class("wizard-navigation"))
        self.assertTrue(footer.back_button.get_mapped())
        self.assertFalse(footer.next_button.get_visible())
        ok, bounds = footer.compute_bounds(window)
        self.assertTrue(ok)
        self.assertLessEqual(bounds.get_y() + bounds.get_height(), window.get_height())

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
