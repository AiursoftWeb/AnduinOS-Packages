"""The fourth model is selectable without silently accepting unsupported languages."""
import unittest
from unittest.mock import patch
from gi.repository import Adw, Gio, Gtk
from anduinos_whisper_gtk import app


class ModelWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        cls.application = Adw.Application(application_id='com.anduinos.ParakeetSettingsTest')
        cls.application.register(None)

    def setUp(self):
        self.patches = [patch.object(app, 'VoiceServiceClient'), patch.object(app, 'VoiceUiClient'),
                        patch.object(app, '_input_devices', return_value=[]),
                        patch.object(app, 'model_installed', return_value=True)]
        for item in self.patches: item.start()
        self.window = app.SettingsWindow(self.application)
        self.window.settings.set_string('model', 'base')
        self.window.settings.set_string('language', 'zh-Hans')
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for key in ['model', 'language', 'recognition-backend']:
            self.window.settings.reset(key)
        self.window.destroy()
        for item in reversed(self.patches): item.stop()

    def test_fourth_row_shows_download_install_and_memory_cost(self):
        self.assertEqual(len(self.window.model_rows), 4)
        subtitle = self.window.model_rows['parakeet'][0].get_subtitle()
        for expected in ['631', '812', '1.5 GiB']:
            self.assertIn(expected, subtitle)

    def test_chinese_selection_requires_confirmation_then_switches_to_cpu(self):
        shown=[]
        with patch.object(Adw.AlertDialog, 'present', new=lambda dialog, parent: shown.append(dialog)):
            self.window._select_model('parakeet')
        self.assertEqual(self.window.settings.get_string('model'), 'base')
        dialog = shown[0]
        self.assertIsInstance(dialog.get_extra_child(), Adw.ComboRow)
        dialog.emit('response', 'use')
        self.assertEqual(self.window.settings.get_string('model'), 'parakeet')
        self.assertEqual(self.window.settings.get_string('language'), 'auto')
        self.assertEqual(self.window.backend_row.get_selected(), 1)
        self.assertFalse(self.window.backend_row.get_sensitive())
        # Attempting Chinese while Parakeet is active leaves the valid language intact.
        self.window.language_row.set_selected(1)
        self.assertEqual(self.window.settings.get_string('language'), 'auto')
        self.window._select_model('small')
        self.assertTrue(self.window.backend_row.get_sensitive())

    def test_cancel_button_keeps_other_downloads_unchanged(self):
        self.window.downloading_models = {'parakeet', 'small'}
        self.window._refresh_model_row('parakeet')
        button = self.window.model_rows['parakeet'][1]
        self.assertEqual(button.get_label(), 'Cancel')
        with patch.object(self.window.downloader, 'cancel') as cancel:
            button.emit('clicked')
            cancel.assert_called_once_with('parakeet')
