"""Smoke tests for the in-process Software Source window."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


class SoftwareSourceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("CONTROL_PANEL_UI_TESTS") != "1":
            raise RuntimeError("Run the gui test profile")
        from anduinos_control_panel import app, software_sources

        cls.app = app
        cls.ui = software_sources
        app.Adw.init()

    def setUp(self):
        self.owner = self.ui.Adw.Window()
        with patch.object(
            self.ui,
            "current_mirror",
            return_value="https://archive.ubuntu.com/ubuntu/",
        ):
            self.window = self.ui.SoftwareSourceWindow(self.owner)

    def tearDown(self):
        self.window.destroy()
        self.owner.destroy()

    def test_window_keeps_migrated_actions_inside_control_panel(self):
        self.assertEqual(self.window.get_title(), self.ui._("Software Source"))
        self.assertEqual(
            self.window.mirror_button.get_label().strip(),
            self.ui._("  Switch to Fastest Mirror  ").strip(),
        )
        self.assertEqual(
            self.window.update_button.get_label().strip(),
            self.ui._("  Check for Updates  ").strip(),
        )
        self.assertIn(
            "https://archive.ubuntu.com/ubuntu/",
            self.window.current_source.get_label(),
        )
        self.assertFalse(
            self.window.mirror_button.has_css_class("suggested-action")
        )
        self.assertFalse(
            self.window.update_button.has_css_class("suggested-action")
        )

    def test_update_state_is_visible_and_busy_window_cannot_be_closed(self):
        self.window._set_busy(True)
        self.assertFalse(self.window.get_deletable())
        self.assertFalse(self.window.mirror_button.get_sensitive())
        self.window._set_busy(False)
        self.assertTrue(self.window.get_deletable())

        self.window._checked(2)
        self.assertTrue(self.window._updates_available)
        self.assertEqual(
            self.window.update_button.get_label().strip(),
            self.ui._("  Install Updates  ").strip(),
        )
        self.assertEqual(self.window.status.get_label(), self.ui._("Updates are available."))

    def test_control_panel_reuses_one_internal_window(self):
        owner = type("Owner", (), {})()
        owner._software_source_window = None
        with patch.object(self.app, "SoftwareSourceWindow") as window_type:
            self.app.ControlPanelWindow._open_software_source(owner)
            self.app.ControlPanelWindow._open_software_source(owner)
        window_type.assert_called_once_with(owner)
        self.assertEqual(window_type.return_value.present.call_count, 2)


if __name__ == "__main__":
    unittest.main()
