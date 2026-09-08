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


