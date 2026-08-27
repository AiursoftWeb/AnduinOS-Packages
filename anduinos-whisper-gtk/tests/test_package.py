from pathlib import Path
import json
import re
import sys
import unittest
import xml.etree.ElementTree as ET

import gi


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from anduinos_whisper_gtk.shortcuts import accelerator_from_key_event  # noqa: E402


class PackageTests(unittest.TestCase):
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
