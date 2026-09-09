from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_whisper_gtk.app import SettingsWindow


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
        window.settings.set_boolean.assert_called_once_with("full-tuning-pending", True)
        window.settings.reset.assert_not_called()
        window.client.assert_not_called()

    def test_reset_only_resets_performance_settings(self):
        window = Mock()
        SettingsWindow._reset_performance(window, None)
        window.settings.reset.assert_called_once_with("recognition-threads")
        window._retest_performance.assert_called_once_with(None)
