from pathlib import Path
import json
import re
import subprocess
import sys
import unittest
from unittest import mock

import gi

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

from anduinos_whisper_gtk.shortcuts import accelerator_from_key_event  # noqa: E402
from anduinos_whisper_gtk import app as settings_app  # noqa: E402

class PackageTests(unittest.TestCase):
    def test_finish_and_cancel_controller_behavior(self):
        subprocess.run(['node', str(ROOT / 'tests/test_finishing.mjs')], check=True)

    def test_importing_settings_does_not_load_the_audio_backend(self):
        self.assertNotIn("anduinos_whisper_framework.audio", sys.modules)

    def test_python_sources_compile(self):
        self.assertEqual(list((ROOT / "src").rglob("*.pyc")), [])
        self.assertEqual(list((ROOT / "src").rglob("__pycache__")), [])
        for source in [
            ROOT / "src/anduinos-whisper-gtk",
            *sorted((ROOT / "src/anduinos_whisper_gtk").glob("*.py")),
        ]:
            compile(source.read_text(), str(source), "exec")

    def test_extension_table_messages_are_extractable(self):
        extension_path = ROOT / "data/voice-typing@anduinos.com/extension.js"
        extension = extension_path.read_text()
        table_sources = set()
        for name in ("STATE_TEXT", "CALIBRATION_TEXT", "LANGUAGE_TEXT"):
            block = re.search(rf"const {name} = \{{(.*?)\n\}};", extension, re.DOTALL)
            self.assertIsNotNone(block)
            self.assertNotRegex(block.group(1), r""":\s*['\"]""")
            values = re.findall(r"N_\('([^']+)'\)", block.group(1))
            self.assertTrue(values)
            table_sources.update(values)
        result = subprocess.run([
            "xgettext", "--language=JavaScript", "--from-code=UTF-8",
            "--keyword=_:1", "--keyword=N_:1", "--no-wrap",
            "--output=-", str(extension_path),
        ], check=True, capture_output=True, text=True)
        for message in table_sources:
            self.assertIn(f"msgid {json.dumps(message, ensure_ascii=False)}", result.stdout)

    def test_missing_shell_extension_requests_a_new_login(self):
        window = mock.Mock()
        window.ui_client.state.side_effect = GLib.Error.new_literal(
            Gio.dbus_error_quark(),
            "raw D-Bus object-path error",
            Gio.DBusError.UNKNOWN_METHOD,
        )

        result = settings_app.SettingsWindow._load_state(window)

        self.assertEqual(result, GLib.SOURCE_REMOVE)
        window._state_changed.assert_called_once_with(
            "restart-required",
            settings_app.RESTART_REQUIRED_DETAIL,
        )
        self.assertNotIn(
            "raw D-Bus object-path error",
            window._state_changed.call_args.args,
        )

    def test_restart_required_state_disables_start(self):
        window = mock.Mock(testing=False)

        settings_app.SettingsWindow._state_changed(
            window,
            "restart-required",
            settings_app.RESTART_REQUIRED_DETAIL,
        )

        self.assertEqual(window.ui_state, "restart-required")
        window.status_row.set_title.assert_called_once_with("Sign out required")
        window.status_row.set_subtitle.assert_called_once_with(
            "Sign out and back in to finish enabling Voice Typing."
        )
        window.status_icon.set_from_icon_name.assert_called_once_with(
            "dialog-warning-symbolic"
        )
        window.start_button.set_sensitive.assert_called_once_with(False)

    def test_microphone_test_does_not_reenable_start_before_new_login(self):
        window = mock.Mock(testing=True, ui_state="restart-required")

        settings_app.SettingsWindow._toggle_test(window, mock.Mock())

        self.assertFalse(window.testing)
        window.start_button.set_sensitive.assert_called_once_with(False)

    def test_shortcut_capture_accepts_literal_keys_and_modifiers(self):
        self.assertEqual(
            accelerator_from_key_event(Gdk.KEY_grave, Gdk.ModifierType(0)),
            "grave",
        )
        self.assertEqual(
            accelerator_from_key_event(
                Gdk.KEY_grave, Gdk.ModifierType.SUPER_MASK
            ),
            "<Super>grave",
        )
        self.assertIsNone(
            accelerator_from_key_event(Gdk.KEY_Super_L, Gdk.ModifierType(0))
        )

    @mock.patch.object(settings_app, "model_installed", return_value=False)
    def test_uninstalled_model_click_only_opens_consent(self, _installed):
        window = mock.Mock()

        settings_app.SettingsWindow._model_action(window, mock.Mock(), "tiny")

        window._confirm_model_download.assert_called_once_with("tiny")
        window._start_model_download.assert_not_called()

    def test_only_explicit_download_response_starts_network_work(self):
        window = mock.Mock()

        for response in ("cancel", "close", "escape"):
            settings_app.SettingsWindow._model_download_response(
                window, mock.Mock(), response, "small"
            )
        window._start_model_download.assert_not_called()

        settings_app.SettingsWindow._model_download_response(
            window, mock.Mock(), "download", "small"
        )
        window._start_model_download.assert_called_once_with("small")

    @mock.patch.object(settings_app, "is_user_model", return_value=True)
    def test_downloaded_model_has_a_separate_remove_action(self, _user_model):
        window = mock.Mock()

        settings_app.SettingsWindow._model_remove_action(
            window, mock.Mock(), "tiny"
        )

        window._confirm_remove_model.assert_called_once_with("tiny")

    @mock.patch.object(settings_app, "is_user_model", return_value=False)
    def test_system_model_cannot_be_removed(self, _user_model):
        window = mock.Mock()

        settings_app.SettingsWindow._model_remove_action(
            window, mock.Mock(), "base"
        )

        window._confirm_remove_model.assert_not_called()

    def test_model_selection_refresh_preserves_active_download(self):
        window = mock.Mock()
        row = mock.Mock()
        button = mock.Mock()
        remove_button = mock.Mock()
        progress = mock.Mock()
        window.model_rows = {
            "small": (row, button, remove_button, progress),
        }
        window.downloading_models = {"small"}

        settings_app.SettingsWindow._refresh_model_row(window, "small")

        progress.set_visible.assert_called_once_with(True)
        button.set_sensitive.assert_called_once_with(False)
        button.set_label.assert_called_once_with("Downloading…")
        remove_button.set_visible.assert_called_once_with(False)
        window.settings.get_string.assert_not_called()

    def test_duplicate_download_start_is_ignored(self):
        window = mock.Mock()
        window.downloading_models = {"small"}

        settings_app.SettingsWindow._start_model_download(window, "small")

        window.downloader.download.assert_not_called()

    @mock.patch.object(settings_app, "remove_user_model", return_value=True)
    def test_removing_an_unselected_model_keeps_current_selection(self, _remove):
        window = mock.Mock()
        window.settings.get_string.return_value = "base"

        settings_app.SettingsWindow._remove_model(window, "tiny")

        window.settings.set_string.assert_not_called()
        window._refresh_all_models.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
