"""Isolated XKB translation for the installer's physical-keyboard tester.

The desktop compositor translates key events with the session's active
layout.  The installer must instead preview the layout selected for the target
system without mutating the user's GNOME input sources.  This module consumes
GTK's raw XKB-compatible keycodes and translates them with a private
libxkbcommon state machine.
"""

from __future__ import annotations

import ctypes
import locale
from dataclasses import dataclass
from functools import lru_cache


XKB_CONTEXT_NO_FLAGS = 0
XKB_LOG_LEVEL_CRITICAL = 10
XKB_KEYMAP_COMPILE_NO_FLAGS = 0
XKB_KEYMAP_FORMAT_TEXT_V1 = 1
XKB_KEY_UP = 0
XKB_KEY_DOWN = 1
XKB_STATE_MODS_DEPRESSED = 1 << 0
XKB_STATE_MODS_LATCHED = 1 << 1
XKB_STATE_MODS_LOCKED = 1 << 2
XKB_STATE_MODS_EFFECTIVE = 1 << 3
XKB_STATE_LAYOUT_DEPRESSED = 1 << 4
XKB_STATE_LAYOUT_LATCHED = 1 << 5
XKB_STATE_LAYOUT_LOCKED = 1 << 6
XKB_MOD_INVALID = 0xFFFFFFFF
XKB_COMPOSE_NOTHING = 0
XKB_COMPOSE_COMPOSING = 1
XKB_COMPOSE_COMPOSED = 2
XKB_COMPOSE_CANCELLED = 3


class KeyboardPreviewError(RuntimeError):
    """Raised when the selected XKB layout cannot be previewed safely."""


class _RuleNames(ctypes.Structure):
    _fields_ = (
        ("rules", ctypes.c_char_p),
        ("model", ctypes.c_char_p),
        ("layout", ctypes.c_char_p),
        ("variant", ctypes.c_char_p),
        ("options", ctypes.c_char_p),
    )


@dataclass(frozen=True)
class PreviewResult:
    """One translated key press returned to the GTK entry controller."""

    text: str = ""
    handled: bool = False
    composing: bool = False


@lru_cache(maxsize=1)
def _library():
    try:
        lib = ctypes.CDLL("libxkbcommon.so.0")
    except OSError as error:
        raise KeyboardPreviewError("libxkbcommon is unavailable") from error

    pointer = ctypes.c_void_p
    lib.xkb_context_new.argtypes = (ctypes.c_int,)
    lib.xkb_context_new.restype = pointer
    lib.xkb_context_unref.argtypes = (pointer,)
    lib.xkb_context_set_log_level.argtypes = (pointer, ctypes.c_int)

    lib.xkb_keymap_new_from_names.argtypes = (
        pointer,
        ctypes.POINTER(_RuleNames),
        ctypes.c_int,
    )
    lib.xkb_keymap_new_from_names.restype = pointer
    if hasattr(lib, "xkb_keymap_new_from_names2"):
        lib.xkb_keymap_new_from_names2.argtypes = (
            pointer,
            ctypes.POINTER(_RuleNames),
            ctypes.c_int,
            ctypes.c_int,
        )
        lib.xkb_keymap_new_from_names2.restype = pointer
    lib.xkb_keymap_unref.argtypes = (pointer,)
    lib.xkb_keymap_mod_get_index.argtypes = (pointer, ctypes.c_char_p)
    lib.xkb_keymap_mod_get_index.restype = ctypes.c_uint32

    lib.xkb_state_new.argtypes = (pointer,)
    lib.xkb_state_new.restype = pointer
    lib.xkb_state_unref.argtypes = (pointer,)
    lib.xkb_state_key_get_one_sym.argtypes = (pointer, ctypes.c_uint32)
    lib.xkb_state_key_get_one_sym.restype = ctypes.c_uint32
    lib.xkb_state_key_get_utf8.argtypes = (
        pointer,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_size_t,
    )
    lib.xkb_state_key_get_utf8.restype = ctypes.c_int
    lib.xkb_state_update_key.argtypes = (
        pointer,
        ctypes.c_uint32,
        ctypes.c_int,
    )
    lib.xkb_state_update_key.restype = ctypes.c_int
    lib.xkb_state_update_mask.argtypes = (
        pointer,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
    )
    lib.xkb_state_update_mask.restype = ctypes.c_int
    lib.xkb_state_serialize_mods.argtypes = (pointer, ctypes.c_int)
    lib.xkb_state_serialize_mods.restype = ctypes.c_uint32
    lib.xkb_state_serialize_layout.argtypes = (pointer, ctypes.c_int)
    lib.xkb_state_serialize_layout.restype = ctypes.c_uint32
    lib.xkb_state_mod_name_is_active.argtypes = (
        pointer,
        ctypes.c_char_p,
        ctypes.c_int,
    )
    lib.xkb_state_mod_name_is_active.restype = ctypes.c_int
    lib.xkb_keysym_get_name.argtypes = (
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_size_t,
    )
    lib.xkb_keysym_get_name.restype = ctypes.c_int

    lib.xkb_compose_table_new_from_locale.argtypes = (
        pointer,
        ctypes.c_char_p,
        ctypes.c_int,
    )
    lib.xkb_compose_table_new_from_locale.restype = pointer
    lib.xkb_compose_table_unref.argtypes = (pointer,)
    lib.xkb_compose_state_new.argtypes = (pointer, ctypes.c_int)
    lib.xkb_compose_state_new.restype = pointer
    lib.xkb_compose_state_unref.argtypes = (pointer,)
    lib.xkb_compose_state_feed.argtypes = (pointer, ctypes.c_uint32)
    lib.xkb_compose_state_feed.restype = ctypes.c_int
    lib.xkb_compose_state_get_status.argtypes = (pointer,)
    lib.xkb_compose_state_get_status.restype = ctypes.c_int
    lib.xkb_compose_state_get_utf8.argtypes = (
        pointer,
        ctypes.c_char_p,
        ctypes.c_size_t,
    )
    lib.xkb_compose_state_get_utf8.restype = ctypes.c_int
    lib.xkb_compose_state_reset.argtypes = (pointer,)
    return lib


class XkbKeyboardPreview:
    """Translate physical keycodes with one private XKB layout and state."""

    def __init__(
        self,
        layout: str,
        *,
        model: str = "pc105",
        variant: str = "",
    ) -> None:
        self._context = None
        self._keymap = None
        self._state = None
        self._compose_table = None
        self._compose_state = None
        self._pressed: set[int] = set()
        self._lib = _library()
        self._context = self._lib.xkb_context_new(XKB_CONTEXT_NO_FLAGS)
        if not self._context:
            raise KeyboardPreviewError("Could not create an XKB context")
        self._lib.xkb_context_set_log_level(
            self._context, XKB_LOG_LEVEL_CRITICAL
        )
        self._model = model
        self._compose_table = self._new_compose_table()
        try:
            self.set_layout(layout, variant)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Release native state; repeated calls are safe."""

        if self._compose_state:
            self._lib.xkb_compose_state_unref(self._compose_state)
            self._compose_state = None
        if self._state:
            self._lib.xkb_state_unref(self._state)
            self._state = None
        if self._keymap:
            self._lib.xkb_keymap_unref(self._keymap)
            self._keymap = None
        if self._compose_table:
            self._lib.xkb_compose_table_unref(self._compose_table)
            self._compose_table = None
        if self._context:
            self._lib.xkb_context_unref(self._context)
            self._context = None
        self._pressed.clear()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def set_layout(self, layout: str, variant: str = "") -> None:
        """Atomically replace the preview keymap and reset transient state."""

        names = _RuleNames(
            None,
            self._model.encode(),
            layout.encode(),
            variant.encode() if variant else None,
            None,
        )
        if hasattr(self._lib, "xkb_keymap_new_from_names2"):
            keymap = self._lib.xkb_keymap_new_from_names2(
                self._context,
                ctypes.byref(names),
                XKB_KEYMAP_FORMAT_TEXT_V1,
                XKB_KEYMAP_COMPILE_NO_FLAGS,
            )
        else:
            keymap = self._lib.xkb_keymap_new_from_names(
                self._context,
                ctypes.byref(names),
                XKB_KEYMAP_COMPILE_NO_FLAGS,
            )
        if not keymap:
            raise KeyboardPreviewError(
                f"Could not compile XKB layout {layout!r}"
            )
        state = self._lib.xkb_state_new(keymap)
        if not state:
            self._lib.xkb_keymap_unref(keymap)
            raise KeyboardPreviewError(
                f"Could not create XKB state for {layout!r}"
            )
        compose_state = self._new_compose_state()

        old_compose_state = self._compose_state
        old_state = self._state
        old_keymap = self._keymap
        self._keymap = keymap
        self._state = state
        self._compose_state = compose_state
        self._pressed.clear()
        if old_compose_state:
            self._lib.xkb_compose_state_unref(old_compose_state)
        if old_state:
            self._lib.xkb_state_unref(old_state)
        if old_keymap:
            self._lib.xkb_keymap_unref(old_keymap)

    def reset(self) -> None:
        """Clear modifiers, locks, pressed keys and an unfinished composition."""

        if not self._keymap:
            return
        state = self._lib.xkb_state_new(self._keymap)
        if not state:
            raise KeyboardPreviewError("Could not reset XKB state")
        compose_state = self._new_compose_state()
        if self._state:
            self._lib.xkb_state_unref(self._state)
        if self._compose_state:
            self._lib.xkb_compose_state_unref(self._compose_state)
        self._state = state
        self._compose_state = compose_state
        self._pressed.clear()

    @property
    def composing(self) -> bool:
        if not self._compose_state:
            return False
        return (
            self._lib.xkb_compose_state_get_status(self._compose_state)
            == XKB_COMPOSE_COMPOSING
        )

    def press(self, keycode: int) -> PreviewResult:
        """Translate a GDK raw keycode and update the private keyboard state."""

        if not self._state or keycode <= 0:
            return PreviewResult()
        keysym = self._lib.xkb_state_key_get_one_sym(self._state, keycode)
        text = self._state_utf8(keycode)
        shortcut = self._shortcut_modifier_active()
        repeated = keycode in self._pressed
        if not repeated:
            self._lib.xkb_state_update_key(
                self._state, keycode, XKB_KEY_DOWN
            )
            self._pressed.add(keycode)

        if shortcut:
            return PreviewResult()
        if self._compose_state and keysym:
            self._lib.xkb_compose_state_feed(self._compose_state, keysym)
            status = self._lib.xkb_compose_state_get_status(
                self._compose_state
            )
            if status == XKB_COMPOSE_COMPOSING:
                return PreviewResult(handled=True, composing=True)
            if status == XKB_COMPOSE_COMPOSED:
                composed = self._compose_utf8()
                self._lib.xkb_compose_state_reset(self._compose_state)
                return PreviewResult(
                    text=composed,
                    handled=True,
                )
            if status == XKB_COMPOSE_CANCELLED:
                self._lib.xkb_compose_state_reset(self._compose_state)

        if _printable(text):
            return PreviewResult(text=text, handled=True)
        if self._keysym_name(keysym).startswith("dead_"):
            return PreviewResult(handled=True, composing=True)
        return PreviewResult()

    def sync_modifiers(
        self,
        *,
        shift: bool,
        control: bool,
        alt: bool,
        super_key: bool,
        caps_lock: bool,
    ) -> None:
        """Import modifiers already active when the preview gained focus."""

        if not self._state or not self._keymap:
            return
        # Once this controller has observed a modifier key-down, its private
        # XKB state is authoritative. In particular, do not reinterpret AltGr
        # as a platform-level Alt or Control+Alt compatibility mask.
        if self._pressed:
            return
        depressed = self._lib.xkb_state_serialize_mods(
            self._state, XKB_STATE_MODS_DEPRESSED
        )
        locked = self._lib.xkb_state_serialize_mods(
            self._state, XKB_STATE_MODS_LOCKED
        )
        for name, active in (
            (b"Shift", shift),
            (b"Control", control),
            (b"Mod1", alt),
            (b"Mod4", super_key),
        ):
            depressed = self._set_modifier_mask(depressed, name, active)
        locked = self._set_modifier_mask(locked, b"Lock", caps_lock)
        self._lib.xkb_state_update_mask(
            self._state,
            depressed,
            self._lib.xkb_state_serialize_mods(
                self._state, XKB_STATE_MODS_LATCHED
            ),
            locked,
            self._lib.xkb_state_serialize_layout(
                self._state, XKB_STATE_LAYOUT_DEPRESSED
            ),
            self._lib.xkb_state_serialize_layout(
                self._state, XKB_STATE_LAYOUT_LATCHED
            ),
            self._lib.xkb_state_serialize_layout(
                self._state, XKB_STATE_LAYOUT_LOCKED
            ),
        )

    def release(self, keycode: int) -> None:
        """Release one key without disturbing unrelated held modifiers."""

        if not self._state or keycode not in self._pressed:
            return
        self._lib.xkb_state_update_key(self._state, keycode, XKB_KEY_UP)
        self._pressed.remove(keycode)

    def cancel_composition(self) -> bool:
        """Cancel a pending dead-key/Compose sequence, if one exists."""

        if not self.composing:
            return False
        self._lib.xkb_compose_state_reset(self._compose_state)
        return True

    def _new_compose_table(self):
        for name in _compose_locales():
            table = self._lib.xkb_compose_table_new_from_locale(
                self._context, name.encode(), 0
            )
            if table:
                return table
        return None

    def _new_compose_state(self):
        if not self._compose_table:
            return None
        state = self._lib.xkb_compose_state_new(self._compose_table, 0)
        if not state:
            raise KeyboardPreviewError("Could not create XKB Compose state")
        return state

    def _shortcut_modifier_active(self) -> bool:
        return any(
            self._modifier_active(name)
            for name in (b"Control", b"Mod1", b"Mod4")
        )

    def _modifier_active(self, name: bytes) -> bool:
        return bool(
            self._lib.xkb_state_mod_name_is_active(
                self._state, name, XKB_STATE_MODS_EFFECTIVE
            )
        )

    def _set_modifier_mask(
        self, mask: int, name: bytes, active: bool
    ) -> int:
        index = self._lib.xkb_keymap_mod_get_index(self._keymap, name)
        if index == XKB_MOD_INVALID:
            return mask
        bit = 1 << index
        return mask | bit if active else mask & ~bit

    def _state_utf8(self, keycode: int) -> str:
        return _read_utf8(
            self._lib.xkb_state_key_get_utf8,
            self._state,
            keycode,
        )

    def _compose_utf8(self) -> str:
        return _read_utf8(
            self._lib.xkb_compose_state_get_utf8,
            self._compose_state,
        )

    def _keysym_name(self, keysym: int) -> str:
        if not keysym:
            return ""
        size = self._lib.xkb_keysym_get_name(keysym, None, 0)
        if size <= 0:
            return ""
        buffer = ctypes.create_string_buffer(size + 1)
        self._lib.xkb_keysym_get_name(keysym, buffer, len(buffer))
        return buffer.value.decode("ascii", errors="replace")


def _read_utf8(function, *arguments) -> str:
    size = function(*arguments, None, 0)
    if size <= 0:
        return ""
    buffer = ctypes.create_string_buffer(size + 1)
    function(*arguments, buffer, len(buffer))
    return buffer.value.decode("utf-8", errors="strict")


def _printable(value: str) -> bool:
    return bool(value) and all(character.isprintable() for character in value)


def _compose_locales() -> tuple[str, ...]:
    configured = locale.setlocale(locale.LC_CTYPE)
    return tuple(dict.fromkeys((configured, "C.UTF-8", "C")))
