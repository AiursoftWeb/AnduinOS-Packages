"""libadwaita settings, model management, and microphone calibration."""

from __future__ import annotations

import gettext
import sys

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from anduinos_whisper_framework.audio import input_devices
from anduinos_whisper_framework.config import MODELS, SETTINGS_SCHEMA, model_installed

from .dbus import VoiceServiceClient, VoiceUiClient
from .models import ModelDownloader, is_user_model, remove_user_model
from .shortcuts import accelerator_from_key_event


APP_ID = "com.anduinos.VoiceTyping.Settings"
LOCALE_DIR = "/usr/share/locale"
gettext.bindtextdomain("anduinos-whisper-gtk", LOCALE_DIR)
gettext.textdomain("anduinos-whisper-gtk")
_ = gettext.gettext

LANGUAGES = [
    ("auto", _("Automatic detection")),
    ("zh-Hans", _("Simplified Chinese")),
    ("zh-Hant", _("Traditional Chinese")),
    ("en", _("English")),
    ("es", _("Spanish")),
    ("fr", _("French")),
    ("de", _("German")),
    ("ja", _("Japanese")),
    ("ko", _("Korean")),
    ("ru", _("Russian")),
    ("pt", _("Portuguese")),
]

STATE_LABELS = {
    "closed": _("Voice Typing"),
    "ready": _("Ready"),
    "listening": _("Listening…"),
    "error": _("Needs attention"),
}


class SettingsWindow(Adw.PreferencesWindow):
    def __init__(self, application: Adw.Application):
        super().__init__(
            application=application,
            title=_("Voice Typing"),
            default_width=770,
            default_height=900,
        )
        self.settings = Gio.Settings.new(SETTINGS_SCHEMA)
        self.ui_client = VoiceUiClient()
        self.client = VoiceServiceClient()
        self.downloader = ModelDownloader()
        self.model_rows: dict[str, tuple[Adw.ActionRow, Gtk.Button, Gtk.ProgressBar]] = {}
        self.testing = False
        self._shortcut_dialog: Adw.Dialog | None = None
        self.connect("close-request", self._closing)
        self.ui_client.subscribe(self._state_changed)
        self.client.subscribe("LevelChanged", self._level_changed)
        self._build_general_page()
        self._build_models_page()
        self._build_training_page()
        GLib.idle_add(self._load_state)

    def _build_general_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Settings"), icon_name="preferences-system-symbolic"
        )
        self.add(page)

        status_group = Adw.PreferencesGroup(
            title=_("Voice Typing"),
            description=_(
                "Use the keyboard shortcut to dictate into the focused application. "
                "Speech is processed locally and is never uploaded."
            ),
        )
        self.status_row = Adw.ActionRow(
            title=_("Status"), subtitle=_("Checking the local service…")
        )
        self.status_icon = Gtk.Image.new_from_icon_name(APP_ID)
        self.status_row.add_prefix(self.status_icon)
        self.start_button = Gtk.Button(label=_("Start"), valign=Gtk.Align.CENTER)
        self.start_button.add_css_class("suggested-action")
        self.start_button.connect(
            "clicked", lambda _button: self.ui_client.call("Toggle")
        )
        self.status_row.add_suffix(self.start_button)
        status_group.add(self.status_row)
        page.add(status_group)

        input_group = Adw.PreferencesGroup(title=_("Input"))
        self.microphones = [("", _("System default"), True), *input_devices()]
        microphone_names = Gtk.StringList.new([item[1] for item in self.microphones])
        self.microphone_row = Adw.ComboRow(
            title=_("Microphone"), model=microphone_names
        )
        selected_microphone = self.settings.get_string("microphone")
        for index, (node, _label, _default) in enumerate(self.microphones):
            if node == selected_microphone:
                self.microphone_row.set_selected(index)
                break
        self.microphone_row.connect("notify::selected", self._microphone_changed)
        input_group.add(self.microphone_row)

        language_names = Gtk.StringList.new([item[1] for item in LANGUAGES])
        self.language_row = Adw.ComboRow(title=_("Language"), model=language_names)
        language = self.settings.get_string("language") or "auto"
        if language == "zh":
            language = "zh-Hans"
        self.language_row.set_selected(
            next((index for index, item in enumerate(LANGUAGES) if item[0] == language), 0)
        )
        self.language_row.connect("notify::selected", self._language_changed)
        input_group.add(self.language_row)
        page.add(input_group)

        behavior = Adw.PreferencesGroup(title=_("Behavior"))
        self.shortcut_row = Adw.ActionRow(
            title=_("Keyboard shortcut"),
            subtitle=_("Activate voice typing from anywhere"),
        )
        self.shortcut_label = Gtk.ShortcutLabel(
            valign=Gtk.Align.CENTER,
            disabled_text=_("Disabled"),
        )
        self.shortcut_row.add_suffix(self.shortcut_label)
        change_shortcut = Gtk.Button(
            label=_("Change…"),
            valign=Gtk.Align.CENTER,
            margin_start=12,
        )
        change_shortcut.connect("clicked", self._begin_shortcut_capture)
        self.shortcut_row.add_suffix(change_shortcut)
        self.shortcut_row.set_activatable_widget(change_shortcut)
        self._refresh_shortcut()
        behavior.add(self.shortcut_row)
        for title, subtitle, key in (
            (
                _("Automatic punctuation"),
                _("Keep punctuation recognized from speech"),
                "automatic-punctuation",
            ),
            (
                _("Voice commands"),
                _("Recognize commands such as “new line” and “comma”"),
                "voice-commands",
            ),
            (
                _("Live transcription"),
                _("Show words in the microphone bar while you speak"),
                "live-transcription",
            ),
            (
                _("Final result preview"),
                _("Pause briefly before inserting the completed phrase"),
                "show-preview",
            ),
            (
                _("Start and stop sounds"),
                _("Play a subtle microphone cue"),
                "audio-cues",
            ),
        ):
            row = Adw.SwitchRow(title=title, subtitle=subtitle)
            self.settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)
            behavior.add(row)
        page.add(behavior)

    def _build_models_page(self) -> None:
        page = Adw.PreferencesPage(title=_("Models"), icon_name="folder-download-symbolic")
        self.add(page)
        group = Adw.PreferencesGroup(
            title=_("Offline speech models"),
            description=_(
                "The Base multilingual model is installed with Voice Typing. "
                "Optional models are verified before use."
            ),
        )
        page.add(group)
        for key, model in MODELS.items():
            size = _format_size(model.size)
            row = Adw.ActionRow(title=_(model.title), subtitle=f"{_(model.description)} · {size}")
            progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=110)
            progress.set_visible(False)
            button = Gtk.Button(valign=Gtk.Align.CENTER)
            button.connect("clicked", self._model_action, key)
            row.add_suffix(progress)
            row.add_suffix(button)
            group.add(row)
            self.model_rows[key] = (row, button, progress)
            self._refresh_model_row(key)

    def _build_training_page(self) -> None:
        page = Adw.PreferencesPage(
            title=_("Microphone Training"), icon_name=APP_ID
        )
        self.add(page)
        group = Adw.PreferencesGroup(
            title=_("Calibrate your speaking setup"),
            description=_(
                "Whisper does not need a personal voice profile. Use this page to "
                "check microphone level, distance, and background noise."
            ),
        )
        page.add(group)
        phrase = Adw.ActionRow(
            title=_("Practice phrase"),
            subtitle=_("“Voice typing makes writing faster and more accessible.”"),
        )
        phrase.add_prefix(Gtk.Image.new_from_icon_name("accessories-dictionary-symbolic"))
        group.add(phrase)

        meter_row = Adw.ActionRow(
            title=_("Input level"),
            subtitle=_("Speak normally; aim for the middle of the meter"),
        )
        self.meter = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=180)
        self.meter.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Microphone input level")]
        )
        meter_row.add_suffix(self.meter)
        group.add(meter_row)

        self.test_row = Adw.ActionRow(
            title=_("Microphone test"),
            subtitle=_("No audio is saved during this test"),
        )
        self.test_button = Gtk.Button(label=_("Start Test"), valign=Gtk.Align.CENTER)
        self.test_button.connect("clicked", self._toggle_test)
        self.test_row.add_suffix(self.test_button)
        group.add(self.test_row)

        tips = Adw.PreferencesGroup(title=_("Tips for better recognition"))
        for title, subtitle, icon in (
            (
                _("Speak naturally"),
                _("Use short phrases and pause briefly between sentences"),
                "audio-speakers-symbolic",
            ),
            (
                _("Reduce background noise"),
                _("Keep the microphone close without speaking directly into it"),
                "weather-clear-night-symbolic",
            ),
            (
                _("Choose the language"),
                _("A fixed language is faster than automatic detection"),
                "preferences-desktop-locale-symbolic",
            ),
        ):
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon))
            tips.add(row)
        page.add(tips)

    def _load_state(self) -> bool:
        try:
            self._state_changed(*self.ui_client.state())
        except GLib.Error as error:
            self._state_changed("error", error.message)
        return GLib.SOURCE_REMOVE

    def _state_changed(self, state: str, detail: str) -> None:
        self.status_row.set_subtitle(detail or STATE_LABELS.get(state, state))
        if state == "listening":
            button_label = _("Stop")
        else:
            button_label = _("Start")
        self.start_button.set_label(button_label)
        if state == "error":
            self.status_icon.set_from_icon_name("dialog-warning-symbolic")
        else:
            self.status_icon.set_from_icon_name(APP_ID)
        self.status_row.set_title(STATE_LABELS.get(state, _("Voice Typing")))
        self.start_button.set_sensitive(not self.testing)

    def _level_changed(self, level: float) -> None:
        self.meter.set_fraction(max(0.0, min(1.0, level)))

    def _microphone_changed(self, row: Adw.ComboRow, _parameter) -> None:
        selected = min(row.get_selected(), len(self.microphones) - 1)
        self.settings.set_string("microphone", self.microphones[selected][0])

    def _language_changed(self, row: Adw.ComboRow, _parameter) -> None:
        selected = min(row.get_selected(), len(LANGUAGES) - 1)
        self.settings.set_string("language", LANGUAGES[selected][0])

    def _refresh_shortcut(self) -> None:
        shortcuts = self.settings.get_strv("toggle-shortcut")
        self.shortcut_label.set_accelerator(shortcuts[0] if shortcuts else "")

    def _begin_shortcut_capture(self, _button: Gtk.Button) -> None:
        if self._shortcut_dialog is not None:
            self._shortcut_dialog.present(self)
            return
        previous = self.settings.get_strv("toggle-shortcut")
        committed = {"value": False}
        self.settings.set_strv("toggle-shortcut", [])
        Gio.Settings.sync()

        dialog = Adw.Dialog(
            title=_("Set Keyboard Shortcut"),
            content_width=460,
            content_height=300,
        )
        self._shortcut_dialog = dialog
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        dialog.set_child(toolbar)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=28,
            margin_bottom=28,
            margin_start=28,
            margin_end=28,
            valign=Gtk.Align.CENTER,
        )
        toolbar.set_content(content)
        keyboard = Gtk.Image.new_from_icon_name("input-keyboard-symbolic")
        keyboard.set_pixel_size(48)
        content.append(keyboard)
        heading = Gtk.Label(label=_("Press the new shortcut"))
        heading.add_css_class("title-2")
        content.append(heading)
        description = Gtk.Label(
            label=_("The keys you press will be used to start or stop Voice Typing."),
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        description.add_css_class("dim-label")
        content.append(description)
        waiting = Gtk.ShortcutLabel(
            accelerator="",
            disabled_text=_("Waiting for input…"),
            halign=Gtk.Align.CENTER,
        )
        waiting.add_css_class("card")
        content.append(waiting)
        hint = Gtk.Label(
            label=_("Esc cancels · Backspace disables the shortcut"),
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        content.append(hint)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def key_pressed(
            _controller: Gtk.EventControllerKey,
            keyval: int,
            _keycode: int,
            state: Gdk.ModifierType,
        ) -> bool:
            modifiers = state & Gtk.accelerator_get_default_mod_mask()
            if keyval == Gdk.KEY_Escape and not modifiers:
                dialog.close()
                return True
            if keyval == Gdk.KEY_BackSpace and not modifiers:
                committed["value"] = True
                self.settings.set_strv("toggle-shortcut", [])
                self._refresh_shortcut()
                dialog.close()
                return True
            accelerator = accelerator_from_key_event(keyval, state)
            if accelerator is None:
                return True
            committed["value"] = True
            self.settings.set_strv("toggle-shortcut", [accelerator])
            self._refresh_shortcut()
            dialog.close()
            return True

        keys.connect("key-pressed", key_pressed)
        dialog.add_controller(keys)

        def closed(_dialog: Adw.Dialog) -> None:
            if not committed["value"]:
                self.settings.set_strv("toggle-shortcut", previous)
            self._refresh_shortcut()
            self._shortcut_dialog = None

        dialog.connect("closed", closed)
        dialog.present(self)

    def _model_action(self, _button: Gtk.Button, key: str) -> None:
        if model_installed(key):
            if self.settings.get_string("model") != key:
                self.settings.set_string("model", key)
                self._refresh_all_models()
            elif is_user_model(key):
                self._confirm_remove_model(key)
            return
        row, button, progress = self.model_rows[key]
        button.set_sensitive(False)
        button.set_label(_("Downloading…"))
        progress.set_visible(True)
        progress.set_fraction(0.0)

        def updated(fraction: float) -> bool:
            progress.set_fraction(fraction)
            return GLib.SOURCE_REMOVE

        def completed() -> bool:
            self.settings.set_string("model", key)
            self._refresh_all_models()
            return GLib.SOURCE_REMOVE

        def failed(message: str) -> bool:
            progress.set_visible(False)
            button.set_sensitive(True)
            button.set_label(_("Retry"))
            dialog = Adw.AlertDialog(heading=_("Model download failed"), body=message)
            dialog.add_response("close", _("Close"))
            dialog.present(self)
            return GLib.SOURCE_REMOVE

        self.downloader.download(key, updated, completed, failed)

    def _confirm_remove_model(self, key: str) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove %s model?") % _(MODELS[key].title),
            body=_("The model can be downloaded again later."),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _dialog, response: (
                self._remove_model(key) if response == "remove" else None
            ),
        )
        dialog.present(self)

    def _remove_model(self, key: str) -> None:
        if remove_user_model(key):
            self.settings.set_string("model", "base")
            self._refresh_all_models()

    def _refresh_all_models(self) -> None:
        for key in MODELS:
            self._refresh_model_row(key)

    def _refresh_model_row(self, key: str) -> None:
        _row, button, progress = self.model_rows[key]
        progress.set_visible(False)
        button.set_sensitive(True)
        selected = self.settings.get_string("model") == key
        if selected and model_installed(key):
            button.set_label(_("In Use"))
            button.add_css_class("suggested-action")
        elif model_installed(key):
            button.set_label(_("Use"))
            button.remove_css_class("suggested-action")
        else:
            button.set_label(_("Download"))
            button.remove_css_class("suggested-action")

    def _toggle_test(self, _button: Gtk.Button) -> None:
        self.testing = not self.testing
        self.client.call("StartTest" if self.testing else "StopTest")
        self.test_button.set_label(_("Stop Test") if self.testing else _("Start Test"))
        self.start_button.set_sensitive(not self.testing)
        if not self.testing:
            self.meter.set_fraction(0.0)

    def _closing(self, _window) -> bool:
        if self.testing:
            try:
                self.client.call_sync("StopTest")
            except GLib.Error:
                pass
        self.client.close()
        self.ui_client.close()
        return False


class VoiceTypingApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        window = self.get_active_window() or SettingsWindow(self)
        window.present()

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._about)
        self.add_action(about)

    def _about(self, _action, _parameter) -> None:
        dialog = Adw.AboutDialog()
        dialog.set_application_name(_("AnduinOS Voice Typing"))
        dialog.set_application_icon(APP_ID)
        dialog.set_developer_name(_("AnduinOS Team"))
        dialog.set_version("2.0.2")
        dialog.set_comments(_("Private, offline speech-to-text for the whole desktop."))
        dialog.set_website("https://www.anduinos.com")
        dialog.set_issue_url("https://github.com/AiursoftWeb/AnduinOS/issues")
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.present(self.get_active_window())


def _format_size(size: int) -> str:
    return _("%.0f MB") % (size / 1_000_000)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"--toggle", "--start", "--stop"}:
        method = {"--toggle": "Toggle", "--start": "Start", "--stop": "Stop"}[sys.argv[1]]
        try:
            VoiceUiClient().call_sync(method)
            return 0
        except GLib.Error as error:
            print(error.message, file=sys.stderr)
            return 1
    Adw.init()
    return VoiceTypingApplication().run(sys.argv)
