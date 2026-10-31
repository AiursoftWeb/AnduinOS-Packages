import unittest
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gi.repository import Gio, GLib

from keyboard_preview import XkbKeyboardPreview
from keyboard_session import (
    KeyboardSessionError,
    apply_keyboard_layout,
    keyboard_layout_is_active,
)


class KeyboardSessionTests(unittest.TestCase):
    def settings(self):
        return Gio.Settings.new_with_backend(
            'org.gnome.desktop.input-sources', Gio.memory_settings_backend_new()
        )

    def test_selected_layout_replaces_old_source_engine_mru_and_options(self):
        settings = self.settings()
        settings.set_value('sources', GLib.Variant('a(ss)', [('ibus', 'rime'), ('xkb', 'us')]))
        settings.set_value('mru-sources', GLib.Variant('a(ss)', [('ibus', 'rime')]))
        settings.set_strv('xkb-options', ['ctrl:swapcaps'])
        signature = apply_keyboard_layout('ch', settings=settings)
        self.assertEqual(settings.get_value('sources').unpack(), [('xkb', 'ch')])
        self.assertEqual(settings.get_value('mru-sources').unpack(), [('xkb', 'ch')])
        self.assertEqual(settings.get_strv('xkb-options'), [])
        self.assertEqual(settings.get_string('xkb-model'), 'pc105')
        self.assertFalse(settings.get_has_unapplied())
        self.assertEqual(signature[51][0], ord('$'))

    def test_variant_is_applied_and_changes_the_expected_keymap(self):
        settings = self.settings()
        signature = apply_keyboard_layout('us', 'intl', settings=settings)
        self.assertEqual(settings.get_value('sources').unpack(), [('xkb', 'us+intl')])
        with XkbKeyboardPreview('us') as keyboard:
            self.assertNotEqual(signature, keyboard.keymap_signature())

    def test_keymap_validation_does_not_inherit_process_xkb_defaults(self):
        with XkbKeyboardPreview('ch') as keyboard:
            expected = keyboard.keymap_signature()
        with patch.dict(os.environ, {'XKB_DEFAULT_LAYOUT': 'ru',
                                     'XKB_DEFAULT_VARIANT': 'intl',
                                     'XKB_DEFAULT_OPTIONS': 'ctrl:swapcaps'}):
            actual = apply_keyboard_layout('ch', settings=self.settings())
        self.assertEqual(actual, expected)

    def test_invalid_pair_does_not_write_any_settings(self):
        settings = Mock()
        with self.assertRaises(KeyboardSessionError):
            apply_keyboard_layout('us', 'qwerty', settings=settings)
        self.assertEqual(settings.mock_calls, [])

    def test_locked_settings_fail_before_any_write(self):
        settings = Mock()
        settings.is_writable.return_value = False
        with self.assertRaises(KeyboardSessionError):
            apply_keyboard_layout('ch', settings=settings)
        settings.delay.assert_not_called()
        settings.set_value.assert_not_called()

    def test_rejected_write_reverts_the_pending_batch(self):
        settings = Mock()
        settings.set_value.side_effect = [True, False]
        with self.assertRaises(KeyboardSessionError):
            apply_keyboard_layout('ch', settings=settings)
        settings.revert.assert_called_once()
        settings.apply.assert_not_called()

    def test_verification_requires_real_first_group_including_variant_levels(self):
        display = Mock()
        signature = {51: {0: ord('$'), 1: 0xA3}}
        keys = [SimpleNamespace(group=group, level=level)
                for group in (0, 1) for level in (0, 1)]
        display.map_keycode.return_value = (True, keys, [ord('\\'), ord('|'), ord('$'), 0xA3])
        self.assertFalse(keyboard_layout_is_active(display, signature))
        display.map_keycode.return_value = (True, keys, [ord('$'), ord('|'), ord('$'), 0xA3])
        self.assertFalse(keyboard_layout_is_active(display, signature))
        display.map_keycode.return_value = (True, keys, [ord('$'), 0xA3, ord('\\'), ord('|')])
        self.assertTrue(keyboard_layout_is_active(display, signature))
        display.map_keycode.return_value = (False, [], [])
        self.assertFalse(keyboard_layout_is_active(display, signature))
        self.assertFalse(keyboard_layout_is_active(None, signature))
        self.assertFalse(keyboard_layout_is_active(display, {}))
