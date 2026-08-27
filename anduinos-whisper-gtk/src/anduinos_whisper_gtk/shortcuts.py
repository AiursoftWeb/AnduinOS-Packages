"""Helpers for recording GTK accelerators from key events."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402


MODIFIER_KEYS = frozenset(
    {
        Gdk.KEY_Shift_L,
        Gdk.KEY_Shift_R,
        Gdk.KEY_Control_L,
        Gdk.KEY_Control_R,
        Gdk.KEY_Alt_L,
        Gdk.KEY_Alt_R,
        Gdk.KEY_Meta_L,
        Gdk.KEY_Meta_R,
        Gdk.KEY_Super_L,
        Gdk.KEY_Super_R,
        Gdk.KEY_Hyper_L,
        Gdk.KEY_Hyper_R,
        Gdk.KEY_ISO_Level3_Shift,
        Gdk.KEY_Mode_switch,
        Gdk.KEY_Caps_Lock,
        Gdk.KEY_Num_Lock,
    }
)


def accelerator_from_key_event(
    keyval: int, state: Gdk.ModifierType
) -> str | None:
    """Return a canonical accelerator, ignoring modifier-only events."""

    if keyval in MODIFIER_KEYS:
        return None
    modifiers = state & Gtk.accelerator_get_default_mod_mask()
    if not Gtk.accelerator_valid(keyval, modifiers):
        return None
    return Gtk.accelerator_name(keyval, modifiers)
