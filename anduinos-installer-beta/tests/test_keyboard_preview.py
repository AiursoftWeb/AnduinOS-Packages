import unittest

from keyboard_preview import KeyboardPreviewError, XkbKeyboardPreview
from languages import KEYBOARD_LAYOUTS


# Linux evdev keycodes use an offset of eight in XKB/GDK key events.
KEY_Q = 16 + 8
KEY_E = 18 + 8
KEY_LEFTCTRL = 29 + 8
KEY_LEFTSHIFT = 42 + 8
KEY_CAPSLOCK = 58 + 8
KEY_RIGHTALT = 100 + 8
KEY_DEAD_CIRCUMFLEX_FR = 26 + 8


def tap(preview: XkbKeyboardPreview, keycode: int):
    result = preview.press(keycode)
    preview.release(keycode)
    return result


class XkbKeyboardPreviewTests(unittest.TestCase):
    def test_every_supported_installer_layout_compiles(self):
        for layout in KEYBOARD_LAYOUTS:
            with self.subTest(layout=layout):
                with XkbKeyboardPreview(layout) as preview:
                    self.assertEqual(tap(preview, KEY_Q).handled, True)

    def test_same_physical_key_changes_immediately_with_layout(self):
        with XkbKeyboardPreview("us") as preview:
            self.assertEqual(tap(preview, KEY_Q).text, "q")
            preview.set_layout("fr")
            self.assertEqual(tap(preview, KEY_Q).text, "a")
            preview.set_layout("ru")
            self.assertEqual(tap(preview, KEY_Q).text, "й")
            preview.set_layout("ara")
            self.assertEqual(tap(preview, KEY_Q).text, "ض")

    def test_shift_caps_lock_and_reset_have_real_xkb_semantics(self):
        with XkbKeyboardPreview("fr") as preview:
            preview.press(KEY_LEFTSHIFT)
            self.assertEqual(tap(preview, KEY_Q).text, "A")
            preview.release(KEY_LEFTSHIFT)
            tap(preview, KEY_CAPSLOCK)
            self.assertEqual(tap(preview, KEY_Q).text, "A")
            preview.reset()
            self.assertEqual(tap(preview, KEY_Q).text, "a")

    def test_altgr_uses_the_selected_layouts_third_level(self):
        with XkbKeyboardPreview("de") as preview:
            preview.press(KEY_RIGHTALT)
            preview.sync_modifiers(
                shift=False,
                control=True,
                alt=True,
                super_key=False,
                caps_lock=False,
            )
            self.assertEqual(tap(preview, KEY_Q).text, "@")
            preview.release(KEY_RIGHTALT)

    def test_dead_key_composes_without_using_the_session_layout(self):
        with XkbKeyboardPreview("fr") as preview:
            dead = tap(preview, KEY_DEAD_CIRCUMFLEX_FR)
            self.assertTrue(dead.handled)
            self.assertTrue(dead.composing)
            self.assertTrue(preview.composing)
            self.assertEqual(tap(preview, KEY_E).text, "ê")
            self.assertFalse(preview.composing)

    def test_backspace_or_escape_can_cancel_an_unfinished_composition(self):
        with XkbKeyboardPreview("fr") as preview:
            tap(preview, KEY_DEAD_CIRCUMFLEX_FR)
            self.assertTrue(preview.cancel_composition())
            self.assertFalse(preview.composing)
            self.assertFalse(preview.cancel_composition())

    def test_control_shortcuts_are_left_for_the_gtk_entry(self):
        with XkbKeyboardPreview("us") as preview:
            preview.press(KEY_LEFTCTRL)
            self.assertFalse(tap(preview, KEY_Q).handled)
            preview.release(KEY_LEFTCTRL)
            self.assertEqual(tap(preview, KEY_Q).text, "q")

    def test_modifiers_held_before_focus_are_synchronized(self):
        with XkbKeyboardPreview("fr") as preview:
            preview.sync_modifiers(
                shift=True,
                control=False,
                alt=False,
                super_key=False,
                caps_lock=False,
            )
            self.assertEqual(tap(preview, KEY_Q).text, "A")
            preview.sync_modifiers(
                shift=False,
                control=True,
                alt=False,
                super_key=False,
                caps_lock=False,
            )
            self.assertFalse(tap(preview, KEY_Q).handled)
            preview.sync_modifiers(
                shift=False,
                control=False,
                alt=False,
                super_key=False,
                caps_lock=True,
            )
            self.assertEqual(tap(preview, KEY_Q).text, "A")

    def test_auto_repeat_does_not_leave_a_key_stuck(self):
        with XkbKeyboardPreview("us") as preview:
            self.assertEqual(preview.press(KEY_Q).text, "q")
            self.assertEqual(preview.press(KEY_Q).text, "q")
            preview.release(KEY_Q)
            self.assertEqual(tap(preview, KEY_Q).text, "q")

    def test_unknown_layout_fails_instead_of_falling_back(self):
        with self.assertRaises(KeyboardPreviewError):
            XkbKeyboardPreview("definitely-not-an-xkb-layout")


if __name__ == "__main__":
    unittest.main()
