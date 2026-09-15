"""Actual GTK widgets, with read-only inventory fixtures and no root operations."""
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk
from anduinos_driver_center import intel_graphics_ui as ui
from anduinos_driver_center.intel_graphics import IntelDevice, IntelSnapshot, PARAMETERS


class IntelPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()
        if Gdk.Display.get_default() is None:
            raise RuntimeError("The GUI profile requires an isolated display")

    def build(self, driver="i915", tokens=(), conflicts=(), pending=False):
        snapshot = IntelSnapshot(
            devices=[IntelDevice("0000:00:02.0", "46a6", "Intel fixture", driver, "pci:fixture", True)],
            kernel="test-kernel", parameters={driver: list(PARAMETERS)},
            loaded_values={f"{driver}.enable_psr": "-1"})
        window = Mock()
        def shell(*args):
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            scroll = Gtk.ScrolledWindow()
            scroll.set_child(content)
            return scroll, content
        window._page_shell.side_effect = shell
        with patch.object(ui, "status", return_value={"tokens": list(tokens), "conflicts": list(conflicts), "pending": pending}), \
             patch.object(ui, "installed_kernels", return_value=["test-kernel"]), \
             patch.object(ui, "can_switch", return_value=False), \
             patch.object(ui.subprocess, "run") as run:
            page = ui.IntelGraphicsPage(window, snapshot)
            run.assert_not_called()
        return page, window

    def widgets(self, widget):
        yield widget
        child = widget.get_first_child()
        while child:
            yield from self.widgets(child)
            child = child.get_next_sibling()

    def test_both_drivers_have_identical_controls_without_authorization(self):
        for driver in ("i915", "xe"):
            with self.subTest(driver=driver):
                page, window = self.build(driver)
                self.assertEqual(page.settings, {f"{driver}.{param}": "auto" for param in PARAMETERS})
                rows = [widget for widget in self.widgets(page) if isinstance(widget, Adw.ComboRow)]
                self.assertEqual(len(rows), 5)
                psr = next(row for row in rows if row.get_title() == "Panel self refresh (PSR)")
                psr.set_selected(1)
                self.assertEqual(page.settings[f"{driver}.enable_psr"], "disabled")
                window._run_action.assert_not_called()

    def test_conflicting_settings_block_apply_but_allow_managed_reset(self):
        page, window = self.build(tokens=["i915.enable_psr=0"], conflicts=["/etc/modprobe.d/user.conf"])
        buttons = {widget.get_label(): widget for widget in self.widgets(page) if isinstance(widget, Gtk.Button)}
        self.assertFalse(buttons["Apply Changes"].get_sensitive())
        self.assertTrue(buttons["Restore default settings"].get_sensitive())
        buttons["Restore default settings"].emit("clicked")
        self.assertEqual(window._run_action.call_args.args[1], ["intel-reset"])

    def test_loaded_parameter_is_not_reported_as_active_psr(self):
        page, _window = self.build(pending=True)
        subtitles = [widget.get_subtitle() for widget in self.widgets(page) if isinstance(widget, Adw.ActionRow)]
        self.assertIn("Loaded parameter: -1", subtitles)
        self.assertIn("Saved settings differ from this boot.", subtitles)
        self.assertIn("Unknown", subtitles)

    def test_refreshing_diagnostics_keeps_unsaved_choices(self):
        page, _window = self.build()
        page.settings["i915.enable_psr"] = "disabled"
        with patch.object(ui, "status", return_value={"tokens": [], "conflicts": []}), \
             patch.object(ui, "installed_kernels", return_value=["test-kernel"]), \
             patch.object(ui, "can_switch", return_value=False):
            page.rebuild(preserve_draft=True)
        psr = next(widget for widget in self.widgets(page)
                   if isinstance(widget, Adw.ComboRow) and widget.get_title() == "Panel self refresh (PSR)")
        self.assertEqual(psr.get_selected(), 1)

    def test_apply_requires_confirmation_and_sends_only_structured_settings(self):
        page, _window = self.build()
        parent = Adw.Window()
        parent._run_action = Mock()
        page.window = parent
        page.settings["i915.enable_psr"] = "disabled"
        button = Gtk.Button(label="Apply Changes")
        try:
            for response in ("cancel", "apply"):
                page.apply_changes(button)
                dialogs = [item for item in Gtk.Window.list_toplevels()
                           if isinstance(item, Adw.MessageDialog) and item.get_transient_for() == parent]
                self.assertEqual(len(dialogs), 1)
                self.assertIn("intel-reset", dialogs[0].get_body())
                dialogs[0].emit("response", response)
                dialogs[0].destroy()
                if response == "cancel":
                    parent._run_action.assert_not_called()
            self.assertEqual(parent._run_action.call_args.args[1], ["intel-apply"])
            payload = json.loads(parent._run_action.call_args.kwargs["stdin"])
            self.assertEqual(payload["settings"]["i915.enable_psr"], "disabled")
        finally:
            parent.destroy()


if __name__ == "__main__":
    unittest.main()
