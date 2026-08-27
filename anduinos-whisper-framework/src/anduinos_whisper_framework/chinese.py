"""Deterministic Chinese script normalization through OpenCC."""

from __future__ import annotations

import atexit
import ctypes
from ctypes.util import find_library
import threading


_CONFIG_BY_LANGUAGE = {
    "zh": "t2s.json",  # Preserve the pre-Hans setting as simplified Chinese.
    "zh-CN": "t2s.json",
    "zh-SG": "t2s.json",
    "zh-Hans": "t2s.json",
    "zh-TW": "s2t.json",
    "zh-HK": "s2t.json",
    "zh-Hant": "s2t.json",
}
_INVALID_POINTER = ctypes.c_void_p(-1).value
_LOCK = threading.Lock()
_LIBRARY: ctypes.CDLL | None = None
_CONVERTERS: dict[str, int] = {}


class ChineseConversionError(RuntimeError):
    pass


def whisper_language(language: str) -> str:
    """Map script-specific UI choices to whisper.cpp's language code."""
    return "zh" if language in _CONFIG_BY_LANGUAGE else language


def normalize_chinese_script(text: str, language: str) -> str:
    """Force recognized Chinese into the selected simplified/traditional script."""
    config = _CONFIG_BY_LANGUAGE.get(language)
    if not text or config is None:
        return text

    with _LOCK:
        library = _get_library()
        converter = _CONVERTERS.get(config)
        if converter is None:
            converter = library.opencc_open(config.encode("utf-8"))
            if converter == _INVALID_POINTER:
                raise ChineseConversionError(_last_error(library))
            _CONVERTERS[config] = converter

        encoded = text.encode("utf-8")
        converted = library.opencc_convert_utf8(converter, encoded, len(encoded))
        if converted == _INVALID_POINTER:
            raise ChineseConversionError(_last_error(library))
        try:
            return ctypes.string_at(converted).decode("utf-8")
        finally:
            library.opencc_convert_utf8_free(converted)


def _get_library() -> ctypes.CDLL:
    global _LIBRARY
    if _LIBRARY is not None:
        return _LIBRARY

    library_name = find_library("opencc")
    if not library_name:
        raise ChineseConversionError("OpenCC is not installed")
    library = ctypes.CDLL(library_name)
    library.opencc_open.argtypes = [ctypes.c_char_p]
    library.opencc_open.restype = ctypes.c_void_p
    library.opencc_close.argtypes = [ctypes.c_void_p]
    library.opencc_close.restype = ctypes.c_int
    library.opencc_convert_utf8.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]
    library.opencc_convert_utf8.restype = ctypes.c_void_p
    library.opencc_convert_utf8_free.argtypes = [ctypes.c_void_p]
    library.opencc_convert_utf8_free.restype = None
    library.opencc_error.argtypes = []
    library.opencc_error.restype = ctypes.c_char_p
    _LIBRARY = library
    return library


def _last_error(library: ctypes.CDLL) -> str:
    message = library.opencc_error()
    return message.decode("utf-8", errors="replace") if message else "OpenCC failed"


def _close_converters() -> None:
    with _LOCK:
        if _LIBRARY is None:
            return
        for converter in _CONVERTERS.values():
            _LIBRARY.opencc_close(converter)
        _CONVERTERS.clear()


atexit.register(_close_converters)
