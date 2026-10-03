"""The progress steps must remain scrollable inside a short window."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pages
from gi.repository import Adw, GLib, Gtk
from installer_core.model import SecureBoot
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


class ProgressPageLayoutTests(unittest.TestCase):
    def test_toggle_preserves_live_progress_and_logs_and_failure_opens_output(self):
        window = Adw.Window(default_width=720, default_height=520)
        self.addCleanup(window.destroy)
        navigation = Adw.NavigationView()
        window.set_content(navigation)
        plan = SimpleNamespace(
            regional=SimpleNamespace(input_methods=()),
            platform=SimpleNamespace(secure_boot=SecureBoot.DISABLED),
        )
        with (patch("pages.ordered_progress_steps", return_value=[
                  ("copy-system", "Copy AnduinOS system")]),
              patch("pages.threading.Thread") as thread,
              patch("pages.GLib.timeout_add_seconds", return_value=0)):
            page = pages.build_progress_page(
                plan, {"lang": "en_US", "development_mode": True}, navigation
            )
        navigation.add(page)
        window.present()
        _settle()
        widgets = list(_descendants(page))
        toggle = next(w for w in widgets if isinstance(w, Gtk.ToggleButton))
        modes = next(w for w in widgets if isinstance(w, Gtk.Stack)
                     and w.get_child_by_name("output") is not None)
        progress = next(w for w in widgets if isinstance(w, Gtk.ProgressBar))
        log_view = next(w for w in widgets if isinstance(w, Gtk.TextView))
        self.assertEqual(modes.get_pages().get_n_items(), 2)
        self.assertEqual(modes.get_visible_child_name(), "discover")
        self.assertFalse(any(isinstance(w, Gtk.Label)
                             and w.get_label() == "Installing AnduinOS"
                             for w in widgets))

        def run(_plan, log, update_progress, update_status):
            log("first line")
            update_progress("copy-system", 2, 5)
            update_status("copy-system", "running", "copying")
            _settle()
            self.assertTrue(any(isinstance(w, Gtk.Label)
                                and w.get_label() == "Copy AnduinOS system"
                                and w.get_mapped() for w in widgets))
            for active in (True, False, True, False):
                toggle.set_active(active)
                _settle()
                self.assertEqual(modes.get_visible_child_name(),
                                 "output" if active else "discover")
                self.assertTrue(progress.get_mapped())
                self.assertAlmostEqual(progress.get_fraction(), .4)
                if active:
                    log_scroll = log_view.get_parent()
                    self.assertIsInstance(log_scroll, Gtk.ScrolledWindow)
                    self.assertEqual(log_view.get_margin_start(), 0)
                    self.assertEqual(log_view.get_margin_end(), 0)
                    self.assertGreaterEqual(log_view.get_width(),
                                            log_scroll.get_width() - 20)
                toggle_ok, toggle_bounds = toggle.compute_bounds(window)
                progress_ok, progress_bounds = progress.compute_bounds(window)
                mode_ok, mode_bounds = modes.compute_bounds(window)
                self.assertTrue(toggle_ok and progress_ok and mode_ok)
                self.assertGreaterEqual(toggle_bounds.get_y(),
                                        mode_bounds.get_y() + mode_bounds.get_height())
                self.assertLessEqual(toggle_bounds.get_y() + toggle_bounds.get_height(),
                                     progress_bounds.get_y())
                self.assertLessEqual(toggle_bounds.get_x() + toggle_bounds.get_width(),
                                     window.get_width())
            log("last line")
            return False, "Simulated copy failure"

        with patch("pages.DevelopmentExecutorClient") as client:
            client.return_value.run.side_effect = run
            thread.call_args.kwargs["target"]()
        _settle()
        self.assertTrue(toggle.get_active())
        self.assertEqual(modes.get_visible_child_name(), "output")
        self.assertTrue(progress.get_mapped())
        buffer = log_view.get_buffer()
        self.assertEqual(buffer.get_text(*buffer.get_bounds(), False),
                         "first line\nlast line\nERROR: Simulated copy failure\n")

    def test_completed_installation_fits_and_both_columns_scroll(self):
        window = Adw.Window(default_width=720, default_height=520)
        self.addCleanup(window.destroy)
        load_visual_style(window.get_display())
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        navigation = Adw.NavigationView()
        toolbar.set_content(navigation)
        window.set_content(toolbar)
        plan = SimpleNamespace(
            regional=SimpleNamespace(input_methods=()),
            platform=SimpleNamespace(secure_boot=SecureBoot.ENABLED),
        )
        steps = [(str(index), f"Step {index}") for index in range(35)]
        fake_thread = lambda target, daemon: SimpleNamespace(start=target)
        with (patch("pages.ordered_progress_steps", return_value=steps),
              patch("pages.DevelopmentExecutorClient") as client,
              patch("pages.threading.Thread", side_effect=fake_thread),
              patch("pages.GLib.timeout_add_seconds", return_value=0)):
            client.return_value.run.return_value = (True, "")
            page = pages.build_progress_page(
                plan, {"lang": "en_US", "development_mode": True}, navigation
            )
        navigation.add(page)
        window.present()
        _settle()

        widgets = list(_descendants(page))
        step_scroll = next(
            widget for widget in widgets
            if isinstance(widget, Gtk.ScrolledWindow)
            and widget.get_min_content_width() == 285
        )
        result_scroll = next(
            widget for widget in widgets
            if isinstance(widget, Gtk.ScrolledWindow)
            and any(child.has_css_class("installer-card")
                    for child in _descendants(widget))
        )
        frames = [widget for widget in widgets
                  if isinstance(widget, Gtk.Frame)
                  and widget.has_css_class("progress-card")]
        progress = next(widget for widget in widgets
                        if isinstance(widget, Gtk.ProgressBar))
        self.assertEqual(len(frames), 2)
        toggle = next(widget for widget in widgets
                      if isinstance(widget, Gtk.ToggleButton)
                      and widget.get_label() == "Output")
        self.assertFalse(toggle.get_active())
        for widget in (result_scroll, progress):
            ok, bounds = widget.compute_bounds(window)
            self.assertTrue(ok)
            self.assertLessEqual(bounds.get_x() + bounds.get_width(),
                                 window.get_width())
            self.assertLessEqual(bounds.get_y() + bounds.get_height(),
                                 window.get_height())

        toggle.set_active(True)
        _settle()
        for widget in (*frames, step_scroll, progress):
            ok, bounds = widget.compute_bounds(window)
            self.assertTrue(ok)
            self.assertLessEqual(bounds.get_x() + bounds.get_width(),
                                 window.get_width())
            self.assertLessEqual(bounds.get_y() + bounds.get_height(),
                                 window.get_height())
        step_adjustment = step_scroll.get_vadjustment()
        step_adjustment.set_value(
            step_adjustment.get_upper() - step_adjustment.get_page_size()
        )
        _settle()
        last_step = next(widget for widget in widgets
                         if isinstance(widget, Gtk.Label)
                         and widget.get_label() == "Step 34")
        _, step_bounds = last_step.compute_bounds(window)
        _, scroll_bounds = step_scroll.compute_bounds(window)
        self.assertLessEqual(step_bounds.get_y() + step_bounds.get_height(),
                             scroll_bounds.get_y() + scroll_bounds.get_height())

        toggle.set_active(False)
        _settle()
        result_adjustment = result_scroll.get_vadjustment()
        result_adjustment.set_value(
            result_adjustment.get_upper() - result_adjustment.get_page_size()
        )
        _settle()
        reboot_button = next(widget for widget in widgets
                             if isinstance(widget, Gtk.Button)
                             and widget.has_css_class("suggested-action")
                             and widget in _descendants(result_scroll))
        _, button_bounds = reboot_button.compute_bounds(window)
        _, result_bounds = result_scroll.compute_bounds(window)
        self.assertLessEqual(button_bounds.get_y() + button_bounds.get_height(),
                             result_bounds.get_y() + result_bounds.get_height())

    def test_last_installation_step_is_reachable_at_720x520(self):
        window = Adw.Window(default_width=720, default_height=520)
        self.addCleanup(window.destroy)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        navigation = Adw.NavigationView()
        toolbar.set_content(navigation)
        window.set_content(toolbar)
        plan = SimpleNamespace(
            regional=SimpleNamespace(input_methods=()),
            platform=SimpleNamespace(secure_boot=SecureBoot.DISABLED),
        )
        steps = [(str(index), f"Step {index}") for index in range(35)]
        with (patch("pages.ordered_progress_steps", return_value=steps),
              patch("pages.threading.Thread"),
              patch("pages.GLib.timeout_add_seconds", return_value=0)):
            page = pages.build_progress_page(
                plan, {"lang": "en_US", "development_mode": True}, navigation
            )
        navigation.add(page)
        window.present()
        _settle()

        widgets = list(_descendants(page))
        toggle = next(widget for widget in widgets
                      if isinstance(widget, Gtk.ToggleButton)
                      and widget.get_label() == "Output")
        self.assertFalse(toggle.get_active())
        toggle.set_active(True)
        _settle()
        step_scroll = next(
            widget for widget in widgets
            if isinstance(widget, Gtk.ScrolledWindow)
            and widget.get_min_content_width() == 285
        )
        adjustment = step_scroll.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        _settle()

        last_step = next(
            widget for widget in widgets
            if isinstance(widget, Gtk.Label) and widget.get_label() == "Step 34"
        )
        scroll_ok, scroll_bounds = step_scroll.compute_bounds(window)
        step_ok, step_bounds = last_step.compute_bounds(window)
        self.assertTrue(scroll_ok)
        self.assertTrue(step_ok)
        self.assertLessEqual(
            scroll_bounds.get_y() + scroll_bounds.get_height(),
            window.get_height(),
        )
        self.assertLessEqual(
            step_bounds.get_y() + step_bounds.get_height(),
            scroll_bounds.get_y() + scroll_bounds.get_height(),
        )
