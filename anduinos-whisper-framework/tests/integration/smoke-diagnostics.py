#!/usr/bin/python3
"""Run with dbus-run-session and PYTHONPATH pointing at an extracted package.

Uses synthetic metadata only. No microphone, recognition, text injection or
desktop-session service is started. The caller must supply a private bus.
"""
import json
import os
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.dont_write_bytecode = True
if os.environ.get("ANDUINOS_ISOLATED_TEST_BUS") != "1":
    raise SystemExit("Use dbus-run-session with ANDUINOS_ISOLATED_TEST_BUS=1")

from gi.repository import Gio, GLib
from anduinos_whisper_framework import APP_ID
from anduinos_whisper_framework.daemon import VoiceTypingService


def export():
    return subprocess.run([sys.executable, "-m", "anduinos_whisper_framework.diagnostic_cli"],
                          capture_output=True, text=True, timeout=5)


# NO_AUTO_START must reject an absent service, even when its system service file
# exists. Register the synthetic Shell name only on this private bus.
assert export().returncode == 1
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
reply = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "RequestName",
                             GLib.Variant("(su)", ("org.gnome.Shell", 4)),
                             GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 2000, None)
assert reply.unpack()[0] == 1
with patch("anduinos_whisper_framework.daemon.AudioCapture", side_effect=AssertionError("Unexpected microphone access")):
    service = VoiceTypingService()
    service.settings.set_boolean("audio-cues", False)
    service.performance.append({"kind": "final", "status": "success", "inference_ms": 123,
                                "text": "PRIVATE SENTENCE", "path": "/private/user", "stderr": "PRIVATE LOG"})
    benchmark = {"kind": "benchmark", "phase": "cold", "status": "success",
                 "backend": "cpu", "threads": 4, "initialization_ms": 100,
                 "inference_ms": 250}
    service.performance.append({**benchmark, "text": "PRIVATE CALIBRATION"})
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        result = None
        while time.monotonic() < deadline:
            result = export()
            if result.returncode == 0:
                break
            time.sleep(0.02)
        assert result is not None and result.returncode == 0, result.stderr if result else "no result"
        report = json.loads(result.stdout)
        assert report["measurements"] == [{"kind": "final", "status": "success", "inference_ms": 123}, benchmark]
        assert "PRIVATE" not in result.stdout and "/private" not in result.stdout
        assert service.capture is None and not service.active and not service.testing
    finally:
        GLib.idle_add(service._quit)
        thread.join(5)
        assert not thread.is_alive()
        assert not service.worker.is_alive()
assert export().returncode == 1
print("Packaged service/CLI D-Bus export passed: no auto-start, no microphone, allow-listed report, clean shutdown")
