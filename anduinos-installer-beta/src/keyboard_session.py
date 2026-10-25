"""Apply the installer's physical keyboard choice to the real GNOME session.

Use one XKB source so a previously active IBus engine or MRU source cannot
silently keep passwords on a different layout. This is a user-session change,
not a privileged system-wide localed/setxkbmap operation. Target installation
configuration remains the executor's responsibility.
"""

from gi.repository import Gio, GLib

from keyboard_layouts import is_valid_xkb_choice, xkb_choice_id
from keyboard_preview import XkbKeyboardPreview


class KeyboardSessionError(RuntimeError):
    """The requested session layout could not be configured."""


def apply_keyboard_layout(layout, variant="", *, settings=None):
    """Configure GNOME and return the expected keymap for async verification."""

    if not is_valid_xkb_choice(layout, variant):
        raise KeyboardSessionError("Unknown XKB layout and variant")
    with XkbKeyboardPreview(layout, variant=variant) as keyboard:
        signature = keyboard.keymap_signature()
    if settings is None:
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.gnome.desktop.input-sources", True) if source else None
        if schema is None:
            raise KeyboardSessionError("GNOME input-source settings are unavailable")
        settings = Gio.Settings.new_full(schema, None, None)
    sources = GLib.Variant("a(ss)", [("xkb", xkb_choice_id(layout, variant))])
    values = {
        "xkb-model": GLib.Variant("s", "pc105"),
        "xkb-options": GLib.Variant("as", []),
        "mru-sources": sources,
        "sources": sources,
    }
    if not all(settings.is_writable(key) for key in values):
        raise KeyboardSessionError("GNOME input-source settings are locked")
    settings.delay()
    for key, value in values.items():
        if not settings.set_value(key, value):
            settings.revert()
            raise KeyboardSessionError(f"Could not set GNOME input-source {key}")
    settings.apply()
    return signature


def keyboard_layout_is_active(display, signature):
    """Check the compositor-provided GDK keymap, not just stored settings.

    With a single requested source GNOME activates group zero. It may append
    a locale fallback group; that must not be mistaken for the active layout.
    """

    if display is None or not signature:
        return False
    for keycode, expected in signature.items():
        found, keys, keyvals = display.map_keycode(keycode)
        if not found:
            return False
        actual = {
            key.level: keyval
            for key, keyval in zip(keys, keyvals)
            if key.group == 0
        }
        if any(actual.get(level) != symbol for level, symbol in expected.items()):
            return False
    return True
