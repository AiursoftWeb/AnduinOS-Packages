#!/usr/bin/python3
"""Isolated real GNOME/GTK/native-worker test with public audio, never a mic.

Pass --payload pointing at the three extracted packages. Does not install or
alter the user's running desktop. Temporary desktop/logs remain for inspection.
"""
import argparse
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
UUID = "voice-typing@anduinos.com"
VOICE = "com.anduinos.VoiceTyping"
VOICE_PATH = "/com/anduinos/VoiceTyping"
UI_PATH = VOICE_PATH + "/UI"


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def guard(stage):
    assert os.environ.get("ANDUINOS_DESKTOP_SMOKE") == "1"
    assert stage.name.startswith("anduinos-shell-e2e.") and stage.stat().st_uid == os.getuid()
    assert Path(os.environ["XDG_RUNTIME_DIR"]) == stage / "runtime"


def service(payload, stage):
    guard(stage)
    from gi.repository import GLib
    from anduinos_whisper_framework import daemon
    from anduinos_whisper_framework.resident import ResidentEngine
    from anduinos_whisper_framework.session_engine import SessionEngine
    from anduinos_whisper_framework.tuning import AutomaticSelector
    from anduinos_whisper_framework.vad import VadEngine
    directory = payload / "usr/share/anduinos-whisper-framework/benchmark"
    sample = next(s for s in json.loads((directory / "manifest.json").read_text())["samples"]
                  if s["file"] == "en-short.wav")
    audio_path = directory / sample["file"]
    assert hashlib.sha256(audio_path.read_bytes()).hexdigest() == sample["sha256"]
    with wave.open(str(audio_path), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        pcm = audio.readframes(audio.getnframes())
    model = payload / "usr/share/anduinos-whisper-framework/models/ggml-base.bin"
    worker = payload / "usr/libexec/anduinos-whisper-worker"

    class PublicCapture:
        def __init__(self, **callbacks):
            self.callback = callbacks["on_chunk_metrics"]
            self.vad = callbacks.get("vad")
            self.timer = 0
        def start(self):
            def deliver():
                self.timer = 0
                self.callback(pcm, {"endpoint_ms": 800, "endpoint_reason": "silence"})
                return GLib.SOURCE_REMOVE
            self.timer = GLib.timeout_add(500, deliver)
        def stop(self, flush=True):
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = 0
            if self.vad is not None:
                self.vad.close()
                self.vad = None

    factory = partial(ResidentEngine, executable=worker)
    with patch.object(daemon, "AudioCapture", PublicCapture), \
            patch.object(daemon, "VadEngine", partial(VadEngine, model=payload / "usr/share/anduinos-whisper-framework/models/ggml-silero-v6.2.0.bin", executable=worker)), \
            patch.object(daemon, "SessionEngine", partial(SessionEngine, resident_factory=factory)), \
            patch.object(daemon, "AutomaticSelector", partial(AutomaticSelector, directory=directory, worker=worker)), \
            patch.object(daemon, "model_path", lambda _model: model), \
            patch.object(daemon, "model_installed", lambda selected: selected == "base" and model.is_file()):
        instance = daemon.VoiceTypingService()
        instance.run()
        assert not instance.worker.is_alive()


def desktop(payload, stage):
    guard(stage)
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    context = GLib.MainContext.default()
    processes = []
    logs = []

    def launch(command, name):
        log = (stage / name).open("w")
        logs.append(log)
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def call(bus, path, interface, method, arguments=None):
        return connection.call_sync(bus, path, interface, method, arguments, None,
                                    Gio.DBusCallFlags.NO_AUTO_START, 2000, None).unpack()

    def wait(predicate, label, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if predicate():
                return
            time.sleep(0.02)
        raise AssertionError(f"Timed out: {label}; inspect isolated logs in {stage}")

    def owns(name):
        return call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                    "NameHasOwner", GLib.Variant("(s)", (name,)))[0]

    def ui_ready():
        try:
            return call("org.gnome.Shell", UI_PATH, VOICE + ".UI", "GetState")[0] == "closed"
        except GLib.Error:
            return False

    window = None
    try:
        shell = launch(["gnome-shell", "--headless", "--wayland", "--no-x11",
                        "--virtual-monitor=1280x800", "--wayland-display=anduinos-test"], "shell.log")
        wait(lambda: owns("org.gnome.Shell"), "Shell bus owner")
        wait(ui_ready, "extension UI registration")
        assert shell.poll() is None
        print("Isolated Shell extension loaded", flush=True)
        voice = launch([sys.executable, str(HERE / "smoke-shell.py"), "--payload", str(payload),
                        "--stage", str(stage), "--service"], "voice.log")
        wait(lambda: owns(VOICE), "test voice service")
        # The ordinary caller must still be rejected; only real Shell controls it.
        try:
            call(VOICE, VOICE_PATH, VOICE, "Start")
            raise AssertionError("Non-Shell caller started dictation")
        except GLib.Error as error:
            assert "AccessDenied" in str(error)

        # PyGObject's legacy Gtk auto-init runs at import time; defer the import
        # until our isolated Wayland compositor is actually accepting clients.
        from gi.repository import Gtk
        Gtk.init()
        window = Gtk.Window(title="Isolated voice acceptance", default_width=600, default_height=250)
        view = Gtk.TextView()
        window.set_child(view)
        window.present()
        view.grab_focus()
        wait(lambda: window.is_active() and view.has_focus(), "GTK text focus")
        states = []
        connection.signal_subscribe(VOICE, VOICE, "StateChanged", VOICE_PATH, None,
                                    Gio.DBusSignalFlags.NONE,
                                    lambda _c, _s, _p, _i, _m, value: states.append(value.unpack()[0]))
        call("org.gnome.Shell", UI_PATH, VOICE + ".UI", "Start")
        wait(lambda: "recognizing" in states, "native request submitted", 20)
        call("org.gnome.Shell", UI_PATH, VOICE + ".UI", "Stop")
        assert call("org.gnome.Shell", UI_PATH, VOICE + ".UI", "GetState")[0] == "ready"
        buffer = view.get_buffer()
        def received():
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        wait(lambda: bool(received()), "native transcript pasted into GTK", 20)
        manifest = json.loads((payload / "usr/share/anduinos-whisper-framework/benchmark/manifest.json").read_text())
        expected = next(s["text"] for s in manifest["samples"] if s["file"] == "en-short.wav")
        words = lambda text: re.findall(r"[^\W_]+", text.casefold())
        assert words(received()) == words(expected), "Inserted public text differs"
        report = json.loads(call(VOICE, VOICE_PATH, VOICE, "GetDiagnostics")[0])
        finals = [r for r in report["measurements"] if r.get("kind") == "final"]
        assert finals and "delivery_ms" in finals[-1]
        assert "preparing" in states and "listening" in states
        previous = received()
        states.clear()
        call("org.gnome.Shell", UI_PATH, VOICE + ".UI", "Start")
        wait(lambda: "recognizing" in states, "second native request", 20)
        call("org.gnome.Shell", UI_PATH, VOICE + ".UI", "Dismiss")
        wait(lambda: voice.poll() is not None, "cancelled service shutdown")
        assert voice.returncode == 0, "Test voice service failed"
        assert received() == previous, "Dismissed request inserted text"
        assert call("org.gnome.Shell", UI_PATH, VOICE + ".UI", "GetState")[0] == "closed"
        print(json.dumps({"passed": True, "public_words_inserted": len(words(previous)),
                          "delivery_ms": finals[-1]["delivery_ms"],
                          "microphone_opened": False, "cancelled_request_inserted": False}))
    finally:
        if window:
            window.destroy()
        for process in reversed(processes):
            stop(process)
        for log in logs:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--service", action="store_true")
    args = parser.parse_args()
    payload = args.payload.resolve()
    if args.service:
        return service(payload, args.stage)
    if args.inside:
        return desktop(payload, args.stage)
    stage = Path(tempfile.mkdtemp(prefix="anduinos-shell-e2e."))
    for name in ("runtime", "config", "cache", "data"):
        (stage / name).mkdir(mode=0o700)
    shutil.copytree(payload / "usr/share/gnome-shell/extensions" / UUID,
                    stage / "data/gnome-shell/extensions" / UUID)
    shutil.copytree("/usr/share/glib-2.0/schemas", stage / "schemas")
    shutil.copy2(payload / "usr/share/glib-2.0/schemas/com.anduinos.voice-typing.gschema.xml", stage / "schemas")
    shutil.copy2(HERE / "shell-smoke.gschema.override", stage / "schemas/zz-voice-test.gschema.override")
    subprocess.run(["glib-compile-schemas", "--strict", str(stage / "schemas")], check=True)
    environment = dict(os.environ)
    for key in ("DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE",
                "SESSION_MANAGER", "XDG_SESSION_ID", "AT_SPI_BUS_ADDRESS"):
        environment.pop(key, None)
    environment.update(ANDUINOS_DESKTOP_SMOKE="1", XDG_RUNTIME_DIR=str(stage / "runtime"),
        XDG_CONFIG_HOME=str(stage / "config"), XDG_CACHE_HOME=str(stage / "cache"),
        XDG_DATA_HOME=str(stage / "data"), GSETTINGS_SCHEMA_DIR=str(stage / "schemas"),
        GSETTINGS_BACKEND="memory", PYTHONDONTWRITEBYTECODE="1", GDK_BACKEND="wayland",
        WAYLAND_DISPLAY="anduinos-test", GIO_USE_VFS="local", GTK_A11Y="none", NO_AT_BRIDGE="1",
        PYTHONPATH=str(payload / "usr/lib/python3/dist-packages"))
    print(f"Isolated desktop test: {stage}", flush=True)
    # Apply isolation BEFORE launching the bus so activated services inherit it.
    process = subprocess.Popen(["dbus-run-session", "--", sys.executable, str(HERE / "smoke-shell.py"),
                                "--payload", str(payload), "--stage", str(stage), "--inside"],
                               env=environment, start_new_session=True)
    try:
        return process.wait(timeout=100)
    finally:
        # The group was created here and contains only this test's children.
        # Clean it up even when startup or a D-Bus/GTK assertion times out.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
