import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_whisper_gtk.app import SettingsWindow
from gi.repository import Adw, Gio, GLib


class PerformanceSettingsTests(unittest.TestCase):
    def test_backend_mapping(self):
        window, row = Mock(), Mock()
        for index, backend in enumerate(("auto", "cpu", "gpu")):
            row.get_selected.return_value = index
            SettingsWindow._backend_changed(window, row, None)
            window.settings.set_string.assert_called_with("recognition-backend", backend)

    @patch("anduinos_whisper_gtk.app.Adw.Toast")
    def test_retest_invalidates_cache_without_changing_model_or_threads(self, _toast):
        window = Mock()
        window.settings.get_uint.return_value = 0xffffffff
        SettingsWindow._retest_performance(window, None)
        window.settings.set_uint.assert_called_once_with("tuning-generation", 0)
        window.settings.set_string.assert_called_once_with("recognition-backend", "auto")
        window.settings.reset.assert_not_called()
        window.client.assert_not_called()

    def test_reset_only_resets_performance_settings(self):
        window = Mock()
        SettingsWindow._reset_performance(window, None)
        window.settings.reset.assert_called_once_with("recognition-threads")
        window._retest_performance.assert_called_once_with(None)

    @unittest.skipUnless(os.environ.get("ANDUINOS_GTK_SMOKE") == "1", "requires isolated GTK display and settings")
    def test_real_widgets_bind_to_isolated_settings(self):
        Adw.init()
        window = SettingsWindow.__new__(SettingsWindow)
        Adw.PreferencesWindow.__init__(window)
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
        window.destroy()
