"""Frozen Python backend for migration comparisons, never shipped or activated.

Reuse the retained frontend helpers rather than maintain duplicate configuration
and diagnostic schemas. Reference-only backend modules live in this directory.
"""
from pathlib import Path

__path__.append(str(Path(__file__).resolve().parents[3] / "src" / __name__))

APP_ID = "com.anduinos.VoiceTyping"
OBJECT_PATH = "/com/anduinos/VoiceTyping"
INTERFACE = APP_ID
VERSION = "2.0.3"
