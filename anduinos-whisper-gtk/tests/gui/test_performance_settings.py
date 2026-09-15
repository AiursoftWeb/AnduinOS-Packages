"""Real settings widgets, executed only by the isolated gui profile."""
import os
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from anduinos_whisper_gtk.app import SettingsWindow
from gi.repository import Adw, Gio, GLib


class PerformanceWidgetsTests(unittest.TestCase):
    def test_live_modes_preserve_independent_preferences_and_legacy_choice(self):
        Adw.init()
        window = SettingsWindow.__new__(SettingsWindow)
        Adw.PreferencesWindow.__init__(window)
        self.addCleanup(window.destroy)
        window.settings = Gio.Settings.new("com.anduinos.voice-typing")
        for key in ("live-transcription-mode", "live-transcription"):
            window.settings.reset(key)
            self.addCleanup(window.settings.reset, key)
        window.settings.set_boolean("show-preview", True)
        window.settings.set_boolean("noise-reduction", False)
        row = window._build_live_row()
        self.assertEqual(row.get_selected(), 0)
        self.assertIsNone(window.settings.get_user_value("live-transcription-mode"))
        window.settings.set_boolean("live-transcription", False)
        self.assertEqual(row.get_selected(), 2)
        self.assertIsNone(window.settings.get_user_value("live-transcription-mode"))
        row.set_selected(1)
        self.assertEqual(window.settings.get_string("live-transcription-mode"), "on")
        row.set_selected(0)
        self.assertEqual(window.settings.get_string("live-transcription-mode"), "auto")
        window.settings.set_string("live-transcription-mode", "off")
        self.assertEqual(row.get_selected(), 2)
        self.assertTrue(window.settings.get_boolean("show-preview"))
        self.assertFalse(window.settings.get_boolean("noise-reduction"))

    @classmethod
    def setUpClass(cls):
        if os.environ.get("ANDUINOS_GTK_SMOKE") != "1":
            raise RuntimeError("Run through the isolated gui profile.")

    def test_real_widgets_bind_to_isolated_settings(self):
        Adw.init()
        window = SettingsWindow.__new__(SettingsWindow)
        Adw.PreferencesWindow.__init__(window)
        self.addCleanup(window.destroy)
        window.settings = Gio.Settings.new("com.anduinos.voice-typing")
        group = window._build_performance_group()
        self.assertIsInstance(group, Adw.PreferencesGroup)
        page = Adw.PreferencesPage()
        page.add(group)
        window.add(page)
        window.present()
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 2
        while window.backend_row.get_width() == 0 and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)
        self.assertTrue(window.backend_row.get_mapped())
        self.assertGreater(window.backend_row.get_width(), 0)
        self.assertGreater(window.threads_row.get_height(), 0)
        window.backend_row.set_selected(1)
        self.assertEqual(window.settings.get_string("recognition-backend"), "cpu")
        window.threads_row.set_value(3)
        self.assertEqual(window.settings.get_uint("recognition-threads"), 3)
        window.settings.set_string("model", "small")
        window.settings.set_string("microphone", "test-microphone")
        window._reset_performance(None)
        self.assertEqual(window.backend_row.get_selected(), 0)
        self.assertEqual(window.threads_row.get_value(), 0)
        self.assertEqual(window.settings.get_string("model"), "small")
        self.assertEqual(window.settings.get_string("microphone"), "test-microphone")

