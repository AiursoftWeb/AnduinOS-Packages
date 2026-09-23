"""Compatibility import for the existing GTK microphone selector.

Capture and speech processing belong to the Rust service, not this module.
"""
from .devices import input_devices

__all__ = ["input_devices"]
