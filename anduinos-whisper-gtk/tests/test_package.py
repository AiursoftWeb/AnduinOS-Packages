from pathlib import Path
import json
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import gi


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

from anduinos_whisper_gtk.shortcuts import accelerator_from_key_event  # noqa: E402
from anduinos_whisper_gtk import app as settings_app  # noqa: E402


class PackageTests(unittest.TestCase):
    def test_importing_settings_does_not_load_the_audio_backend(self):
        self.assertNotIn("anduinos_whisper_framework.audio", sys.modules)

    def test_ui_state_machine_has_exactly_three_states(self):
        extension = (ROOT / "data/voice-typing@anduinos.com/extension.js").read_text()
        state_block = re.search(
            r"const UI_STATE = Object\.freeze\(\{(?P<body>.*?)\}\);",
            extension,
            re.DOTALL,
        )
        self.assertIsNotNone(state_block)
        states = dict(
            re.findall(
                r"^\s*([A-Z_]+):\s*'([^']+)',\s*$",
                state_block.group("body"),
                re.MULTILINE,
            )
        )
        self.assertEqual(
            states,
            {"CLOSED": "closed", "READY": "ready", "LISTENING": "listening"},
        )

        transition_block = re.search(
            r"const TOGGLE_TRANSITION = Object\.freeze\(\{(?P<body>.*?)\}\);",
            extension,
            re.DOTALL,
        )
        self.assertIsNotNone(transition_block)
        transitions = dict(
            re.findall(
                r"^\s*\[UI_STATE\.([A-Z_]+)\]:\s*UI_STATE\.([A-Z_]+),\s*$",
                transition_block.group("body"),
                re.MULTILINE,
            )
        )
        self.assertEqual(
            transitions,
            {"CLOSED": "LISTENING", "READY": "LISTENING", "LISTENING": "READY"},
        )

    def test_python_sources_compile(self):
        self.assertEqual(list((ROOT / "src").rglob("*.pyc")), [])
        self.assertEqual(list((ROOT / "src").rglob("__pycache__")), [])
        for source in [
            ROOT / "src/anduinos-whisper-gtk",
            *sorted((ROOT / "src/anduinos_whisper_gtk").glob("*.py")),
        ]:
            compile(source.read_text(), str(source), "exec")

    def test_projects_are_parseable_and_remain_optional(self):
        gtk_project = ET.parse(ROOT / "anduinos-whisper-gtk.aosproj").getroot()
        framework_project = ET.parse(
            REPOSITORY
            / "anduinos-whisper-framework/anduinos-whisper-framework.aosproj"
        ).getroot()
        self.assertEqual(gtk_project.findtext(".//PackageName"), "anduinos-whisper-gtk")
        self.assertEqual(
            framework_project.findtext(".//PackageName"),
            "anduinos-whisper-framework",
        )
        desktop = (REPOSITORY / "anduinos-desktop/anduinos-desktop.aosproj").read_text()
        core = (REPOSITORY / "anduinos-desktop-core/anduinos-desktop-core.aosproj").read_text()
        self.assertNotIn("anduinos-whisper", desktop)
        self.assertNotIn("anduinos-whisper", core)

    def test_extension_supports_shortcut_overlay_and_desktop_injection(self):
        extension = (ROOT / "data/voice-typing@anduinos.com/extension.js").read_text()
        for contract in (
            "Main.wm.addKeybinding(",
            "Main.layoutManager.addChrome(",
            "reactive: true",
            "global.display.focus_window",
            "create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE)",
            "St.ClipboardType.CLIPBOARD",
            "Clutter.KEY_Control_L",
            "Clutter.KEY_Shift_L",
            "_previewAndInsert(text)",
            "_showPartial(text)",
            "get_boolean('live-transcription')",
            "overlay-x",
            "Clutter.ModifierType.BUTTON1_MASK",
            "captured.get_button() === Clutter.BUTTON_PRIMARY",
            "this._finishDrag();",
            "this._dismissOverlay()",
            "this._bar.add_child(this._preview)",
            "this._root.set_position(x, monitor.y + 4)",
            "Gio.FileIcon",
            "audio-input-microphone.svg",
            "global.connect('shutdown'",
            "this._quitForShellShutdown()",
            "Never activate an unused daemon while the session is closing",
            "const UI_STATE = Object.freeze({",
            "[UI_STATE.CLOSED]: UI_STATE.LISTENING",
            "[UI_STATE.READY]: UI_STATE.LISTENING",
            "[UI_STATE.LISTENING]: UI_STATE.READY",
            "Toggle: () => this._toggleUi()",
            "Start: () => this._startListening()",
            "Stop: () => this._stopListening()",
            "Dismiss: () => this._closeUi()",
            "this._setUiState(UI_STATE.CLOSED, _('Off'))",
            "this._uiState !== UI_STATE.LISTENING",
        ):
            self.assertIn(contract, extension)
        self.assertNotIn("_overlayHidden", extension)
        self.assertNotIn("this._call('Toggle')", extension)
        self.assertNotIn("window-minimize-symbolic", extension)
        self.assertNotIn("_minimized", extension)
        self.assertNotIn("Pause voice typing", extension)
        self.assertNotIn("Resume voice typing", extension)
        self.assertNotIn("PanelMenu", extension)
        self.assertNotIn("PopupMenu", extension)
        self.assertNotIn("addToStatusArea", extension)
        self.assertNotIn("_buildIndicator", extension)

        stylesheet = (
            ROOT / "data/voice-typing@anduinos.com/stylesheet.css"
        ).read_text()
        self.assertIn("min-width: 500px", stylesheet)
        self.assertIn("max-width: 520px", stylesheet)

    def test_extension_metadata_and_settings_schema_are_valid(self):
        metadata = json.loads(
            (ROOT / "data/voice-typing@anduinos.com/metadata.json").read_text()
        )
        self.assertEqual(metadata["uuid"], "voice-typing@anduinos.com")
        self.assertIn("50", metadata["shell-version"])
        schema = ET.parse(
            REPOSITORY
            / "anduinos-whisper-framework/data/com.anduinos.voice-typing.gschema.xml"
        ).getroot()
        keys = {item.attrib["name"] for item in schema.findall(".//key")}
        self.assertTrue(
            {
                "toggle-shortcut",
                "microphone",
                "language",
                "model",
                "voice-commands",
                "audio-cues",
                "show-preview",
                "live-transcription",
            }.issubset(keys)
        )

    def test_settings_offer_microphone_language_models_and_training(self):
        application = (ROOT / "src/anduinos_whisper_gtk/app.py").read_text()
        self.assertIn("default_width=770", application)
        self.assertIn("default_height=900", application)
        for text in (
            'title=_("Microphone")',
                'title=_("Language")',
                '_("Simplified Chinese")',
                '_("Traditional Chinese")',
            'title=_("Keyboard shortcut")',
            'title=_("Models")',
            'title=_("Microphone Training")',
            '_("Live transcription")',
            '_("Final result preview")',
            'self.client.call("StartTest" if self.testing else "StopTest")',
        ):
            self.assertIn(text, application)
        self.assertNotIn('_("Desktop integration")', application)
        self.assertNotIn('_("Floating microphone bar")', application)
        self.assertNotIn("_enable_extension", application)
        self.assertNotIn("org.gnome.Shell.Extensions.ReloadExtension", application)
        self.assertNotIn('("zh", _("Chinese"))', application)
        self.assertIn('self.ui_client.call("Toggle")', application)
        self.assertNotIn('self.client.call("Toggle")', application)
        self.assertNotIn("VoiceServiceClient().call_sync(method)", application)

        dbus = (ROOT / "src/anduinos_whisper_gtk/dbus.py").read_text()
        self.assertIn("class VoiceUiClient:", dbus)
        self.assertIn('UI_INTERFACE = "com.anduinos.VoiceTyping.UI"', dbus)
        self.assertIn("Gio.DBusProxyFlags.DO_NOT_AUTO_START", dbus)

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
        application = (ROOT / "src/anduinos_whisper_gtk/app.py").read_text()
        self.assertIn("Gtk.EventControllerKey()", application)
        self.assertIn('_("Press the new shortcut")', application)
        self.assertIn("Gdk.KEY_Escape", application)
        self.assertIn("Gdk.KEY_BackSpace", application)
        self.assertNotIn("Press Super + H to dictate", application)
        self.assertNotIn("Adw.EntryRow(title=_(\"Keyboard shortcut\"))", application)

    def test_model_downloads_are_size_and_hash_verified(self):
        source = (ROOT / "src/anduinos_whisper_gtk/models.py").read_text()
        self.assertIn("hashlib.sha256()", source)
        self.assertIn("received != model.size", source)
        self.assertIn("digest.hexdigest() != model.sha256", source)
        self.assertIn("os.replace(partial, destination)", source)

    def test_model_download_requires_hugging_face_disclosure_and_consent(self):
        application = (ROOT / "src/anduinos_whisper_gtk/app.py").read_text()

        self.assertIn("self._confirm_model_download(key)", application)
        self.assertIn("Hugging Face, a third-party ", application)
        self.assertIn("service. AnduinOS is not affiliated", application)
        self.assertIn("not affiliated with, sponsored by, or", application)
        self.assertIn("will receive your public IP address", application)
        self.assertIn("may reveal your approximate location", application)
        self.assertIn("https://huggingface.co/privacy", application)
        self.assertIn('dialog.set_default_response("cancel")', application)
        self.assertIn('dialog.set_close_response("cancel")', application)
        self.assertIn('if response == "download":', application)
        self.assertIn("def _start_model_download", application)

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

    def test_microphone_svg_is_embedded_for_app_and_shell(self):
        app_icon = ROOT / "resources/audio-input-microphone.svg"
        installed_app_icon = (
            ROOT / "resources/com.anduinos.VoiceTyping.Settings.svg"
        )
        shell_icon = (
            ROOT
            / "data/voice-typing@anduinos.com/audio-input-microphone.svg"
        )
        control_icon = (
            REPOSITORY
            / "anduinos-control-panel/resources/icons/audio-input-microphone.svg"
        )
        self.assertTrue(ET.parse(app_icon).getroot().tag.endswith("svg"))
        self.assertEqual(app_icon.read_bytes(), installed_app_icon.read_bytes())
        self.assertEqual(app_icon.read_bytes(), shell_icon.read_bytes())
        self.assertEqual(app_icon.read_bytes(), control_icon.read_bytes())
        project = (ROOT / "anduinos-whisper-gtk.aosproj").read_text()
        self.assertIn(
            'Icon="resources/com.anduinos.VoiceTyping.Settings.svg"', project
        )
        self.assertIn(
            'Include="resources/com.anduinos.VoiceTyping.Settings.svg"', project
        )


if __name__ == "__main__":
    unittest.main()
