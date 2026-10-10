"""Real Secure Boot widgets, without inspecting or changing the host firmware."""

import gettext
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from anduinos_secureboot import ui
from anduinos_secureboot.model import DkmsState, SecureBootState


def widgets(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from widgets(child)
        child = child.get_next_sibling()


class SecureBootPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()
        Adw.init()

    def test_enabled_trusted_system_has_no_preparation_action_for_any_loader_hint(self):
        locales = Path(__file__).resolve().parents[2] / "locale"
        for language in ("en_US", "zh_CN"):
            translate = gettext.translation(
                "anduinos-driver-center", localedir=locales, languages=[language]
            ).gettext
            for loader in ("shim", "unknown", "grub"):
                with self.subTest(language=language, boot_loader=loader):
                    state = SecureBootState(True, True, True, True, "serial", boot_loader=loader)
                    with patch.object(ui, "inspect_secure_boot") as inspect, \
                         patch.object(ui, "run_action") as action:
                        page = ui.create_secure_boot_page(
                            translate=translate, initial_state=(state, DkmsState()),
                        )
                        visible_text = "\n".join(
                            w.get_label() for w in widgets(page)
                            if isinstance(w, Gtk.Label) and w.is_visible()
                        )
                        self.assertIn(translate(
                            "System Trust Established. Third-party drivers will load securely."
                        ), visible_text)
                        self.assertNotIn(translate(ui._PREPARE), visible_text)
                        self.assertNotIn(translate(ui._BOOT_WARNING), visible_text)
                        self.assertFalse(any(
                            w.is_visible() for w in widgets(page) if isinstance(w, Gtk.Button)
                        ))
                        inspect.assert_not_called()
                        action.assert_not_called()

    def test_disabled_and_setup_mode_preserve_enrollment_and_explain_firmware(self):
        locales = Path(__file__).resolve().parents[2] / "locale"
        for language in ("en_US", "zh_CN"):
            translate = gettext.translation(
                "anduinos-driver-center", localedir=locales, languages=[language]
            ).gettext
            for setup_mode in (False, True):
                with self.subTest(language=language, setup_mode=setup_mode):
                    state = SecureBootState(
                        False, True, True, True, "serial",
                        setup_mode=setup_mode, boot_loader="shim",
                    )
                    firmware = Mock()
                    with patch.object(ui, "inspect_secure_boot") as inspect, \
                         patch.object(ui, "run_action") as action:
                        page = ui.create_secure_boot_page(
                            translate=translate, initial_state=(state, DkmsState()),
                            firmware_setup=firmware,
                        )
                        labels = [w for w in widgets(page) if isinstance(w, Gtk.Label)]
                        visible_text = "\n".join(w.get_label() for w in labels if w.is_visible())
                        self.assertIn(translate(ui._READY_TO_ENABLE), visible_text)
                        self.assertNotIn(translate(ui._BOOT_WARNING), visible_text)
                        self.assertNotIn(translate(ui._PREPARE_FIRST), visible_text)
                        instructions = (ui._SETUP_MODE_INSTRUCTIONS if setup_mode
                                        else ui._FIRMWARE_SETUP_INSTRUCTIONS)
                        self.assertIn(translate(instructions), visible_text)
                        warning = next(w for w in labels if w.get_label() == (
                            translate(ui._SETUP_MODE) if setup_mode else ""))
                        self.assertEqual(warning.get_visible(), setup_mode)
                        row = next(w for w in widgets(page) if isinstance(w, Adw.ActionRow)
                                   and w.get_title() == translate("Secure Boot"))
                        self.assertEqual(row.get_subtitle(), translate("Secure Boot is disabled"))
                        button = next(w for w in widgets(row) if isinstance(w, Gtk.Button)
                                      and w.get_label() == translate(
                                          ui._FIRMWARE_SETTINGS_BUTTON if setup_mode
                                          else ui._FIRMWARE_SETUP_BUTTON))
                        self.assertTrue(button.get_visible())
                        parent = Adw.Window()
                        parent.set_content(page)
                        try:
                            button.emit("clicked")
                            dialog = next(w for w in Gtk.Window.list_toplevels()
                                          if isinstance(w, Adw.MessageDialog)
                                          and w.get_transient_for() is parent)
                            self.assertEqual(dialog.get_body(), translate(instructions))
                            self.assertEqual(dialog.get_default_response(), "cancel")
                            dialog.response("cancel")
                            dialog.destroy()
                        finally:
                            parent.destroy()
                        firmware.assert_not_called()
                        inspect.assert_not_called()
                        action.assert_not_called()
