"""Session D-Bus service for offline, phrase-by-phrase voice typing."""

from __future__ import annotations

import queue
import sys
import threading
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import APP_ID, INTERFACE, OBJECT_PATH
from .audio import AudioCapture
from .commands import apply_voice_command, remove_punctuation
from .config import SETTINGS_SCHEMA, model_installed, model_path
from .engine import WhisperEngine


INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Start"/>
    <method name="Stop"/>
    <method name="Quit"/>
    <method name="StartTest"/>
    <method name="StopTest"/>
    <method name="GetState">
      <arg name="state" type="s" direction="out"/>
      <arg name="detail" type="s" direction="out"/>
    </method>
    <signal name="StateChanged">
      <arg name="state" type="s"/>
      <arg name="detail" type="s"/>
    </signal>
    <signal name="LevelChanged"><arg name="level" type="d"/></signal>
    <signal name="Transcript">
      <arg name="text" type="s"/>
      <arg name="final" type="b"/>
    </signal>
  </interface>
</node>
"""

SHELL_BUS_NAME = "org.gnome.Shell"
SHELL_CONTROL_METHODS = frozenset({"Start", "Stop", "Quit"})


class VoiceTypingService:
    def __init__(self) -> None:
        self.loop = GLib.MainLoop()
        self.settings = Gio.Settings.new(SETTINGS_SCHEMA)
        self.connection: Gio.DBusConnection | None = None
        self.registration_id = 0
        self.owner_id = 0
        self.shell_watch_id = 0
        self.shell_owner = ""
        self.capture: AudioCapture | None = None
        self.state = "idle"
        self.detail = "Ready"
        self.active = False
        self.testing = False
        self.pending = 0
        self.session_id = 0
        self.partial_generation = 0
        self.partial_floor = 0
        self.work_sequence = 0
        self.work_lock = threading.Lock()
        self.shutting_down = False
        self.last_activity = time.monotonic()
        self.audio_queue: queue.PriorityQueue[
            tuple[int, int, str, int, int, bytes]
        ] = queue.PriorityQueue()
        self.worker = threading.Thread(target=self._recognition_worker, daemon=True)
        self.worker.start()

    def run(self) -> int:
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.shell_owner = self._get_shell_owner()
        self.registration_id = self.connection.register_object(
            OBJECT_PATH,
            node.interfaces[0],
            self._method_called,
            None,
            None,
        )
        self.owner_id = Gio.bus_own_name_on_connection(
            self.connection,
            APP_ID,
            Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
            None,
            self._name_lost,
        )
        self.shell_watch_id = Gio.bus_watch_name_on_connection(
            self.connection,
            SHELL_BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._shell_name_appeared,
            self._shell_name_vanished,
        )
        GLib.timeout_add_seconds(60, self._idle_check)
        self.loop.run()
        return 0

    def _method_called(
        self,
        _connection: Gio.DBusConnection,
        sender: str,
        _object_path: str,
        _interface_name: str,
        method: str,
        _parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        self.last_activity = time.monotonic()
        if method in SHELL_CONTROL_METHODS and sender != self.shell_owner:
            invocation.return_dbus_error(
                f"{APP_ID}.AccessDenied",
                "Dictation state is controlled by the GNOME Shell extension",
            )
            return
        try:
            if method == "Start":
                self.start()
            elif method == "Stop":
                self.stop()
            elif method == "StartTest":
                self.start_test()
            elif method == "StopTest":
                self.stop_test()
            elif method == "Quit":
                self.stop()
                invocation.return_value(None)
                GLib.idle_add(self._quit)
                return
            elif method == "GetState":
                invocation.return_value(GLib.Variant("(ss)", (self.state, self.detail)))
                return
            else:
                invocation.return_dbus_error(
                    f"{APP_ID}.UnknownMethod", f"Unknown method: {method}"
                )
                return
            invocation.return_value(None)
        except Exception as error:
            self._set_state("error", str(error))
            invocation.return_dbus_error(f"{APP_ID}.Error", str(error))

    def start(self) -> None:
        if self.active:
            return
        if self.testing:
            self.stop_test()
        selected_model = self.settings.get_string("model") or "base"
        if not model_installed(selected_model):
            raise RuntimeError("The selected speech model is not installed")
        self.session_id += 1
        self._invalidate_partials()
        session_id = self.session_id
        self.pending = 0
        self.active = True
        self.capture = self._new_capture(session_id=session_id)
        self.capture.start()
        self._play_cue("audio-volume-change")
        self._set_state("listening", "Listening…")

    def stop(self) -> None:
        was_running = self.active or self.testing
        self.active = False
        self.testing = False
        self.session_id += 1
        self._invalidate_partials()
        self.pending = 0
        if self.capture:
            self.capture.stop(flush=False)
            self.capture = None
        if was_running:
            self._play_cue("audio-volume-change")
        self._set_state("idle", "Ready")

    def start_test(self) -> None:
        if self.active:
            self.stop()
        if self.testing:
            return
        self.session_id += 1
        self._invalidate_partials()
        session_id = self.session_id
        self.pending = 0
        self.testing = True
        self.capture = self._new_capture(testing=True, session_id=session_id)
        self.capture.start()
        self._set_state("testing", "Speak to test your microphone")

    def stop_test(self) -> None:
        self.testing = False
        self.session_id += 1
        self._invalidate_partials()
        self.pending = 0
        if self.capture:
            self.capture.stop(flush=False)
            self.capture = None
        self._set_state("idle", "Ready")

    def _new_capture(
        self, testing: bool = False, session_id: int | None = None
    ) -> AudioCapture:
        return AudioCapture(
            microphone=self.settings.get_string("microphone"),
            on_chunk=(lambda _audio: None)
            if testing
            else lambda audio: self._queue_audio(session_id, audio),
            on_partial=(lambda _audio: None)
            if testing
            else lambda audio: self._queue_partial(session_id, audio),
            on_level=lambda level: GLib.idle_add(self._emit_level, level),
            on_error=lambda message: GLib.idle_add(
                self._capture_failed, session_id, message
            ),
            on_no_speech=lambda: GLib.idle_add(self._no_speech, session_id),
            silence_threshold=self.settings.get_double("silence-threshold"),
        )

    def _queue_audio(self, session_id: int | None, pcm: bytes) -> None:
        if session_id != self.session_id or not self.active:
            return
        generation = self._invalidate_partials()
        self.pending += 1
        GLib.idle_add(
            self._set_session_state, session_id, "recognizing", "Recognizing…"
        )
        self._put_work(0, "final", session_id, generation, pcm)

    def _queue_partial(self, session_id: int | None, pcm: bytes) -> None:
        if (
            session_id != self.session_id
            or not self.active
            or not self.settings.get_boolean("live-transcription")
        ):
            return
        generation = self._next_partial()
        self._put_work(1, "partial", session_id, generation, pcm)

    def _invalidate_partials(self) -> int:
        with self.work_lock:
            self.partial_generation += 1
            self.partial_floor = self.partial_generation
            return self.partial_generation

    def _next_partial(self) -> int:
        with self.work_lock:
            self.partial_generation += 1
            return self.partial_generation

    def _put_work(
        self,
        priority: int,
        kind: str,
        session_id: int | None,
        generation: int,
        pcm: bytes,
    ) -> None:
        if session_id is None:
            return
        with self.work_lock:
            sequence = self.work_sequence
            self.work_sequence += 1
        self.audio_queue.put(
            (priority, sequence, kind, session_id, generation, pcm)
        )

    def _partial_should_run(self, session_id: int, generation: int) -> bool:
        with self.work_lock:
            current_generation = self.partial_generation
        return (
            session_id == self.session_id
            and generation == current_generation
            and self.active
            and self.settings.get_boolean("live-transcription")
        )

    def _partial_is_valid(self, session_id: int, generation: int) -> bool:
        with self.work_lock:
            partial_floor = self.partial_floor
        return (
            session_id == self.session_id
            and generation > partial_floor
            and self.active
            and self.settings.get_boolean("live-transcription")
        )

    def _recognition_worker(self) -> None:
        while True:
            _priority, _sequence, kind, session_id, generation, pcm = (
                self.audio_queue.get()
            )
            try:
                if kind == "quit":
                    return
                if session_id != self.session_id:
                    continue
                if kind == "partial" and not self._partial_should_run(
                    session_id, generation
                ):
                    continue
                selected_model = self.settings.get_string("model") or "base"
                engine = WhisperEngine(
                    model_path(selected_model),
                    self.settings.get_string("language") or "auto",
                )
                text = engine.transcribe(pcm)
                if not self.settings.get_boolean("automatic-punctuation"):
                    text = remove_punctuation(text)
                if kind == "partial":
                    GLib.idle_add(
                        self._partial_finished, session_id, generation, text
                    )
                    continue
                text, action = apply_voice_command(
                    text, self.settings.get_boolean("voice-commands")
                )
                GLib.idle_add(self._recognition_finished, session_id, text, action)
            except Exception as error:
                if kind == "final":
                    GLib.idle_add(self._recognition_failed, session_id, str(error))
            finally:
                self.audio_queue.task_done()

    def _partial_finished(
        self, session_id: int, generation: int, text: str
    ) -> bool:
        if self._partial_is_valid(session_id, generation) and text:
            self._emit("Transcript", GLib.Variant("(sb)", (text, False)))
        return GLib.SOURCE_REMOVE

    def _recognition_finished(
        self, session_id: int, text: str, action: str | None
    ) -> bool:
        if session_id != self.session_id:
            return GLib.SOURCE_REMOVE
        self.pending = max(0, self.pending - 1)
        if text:
            self._emit("Transcript", GLib.Variant("(sb)", (text, True)))
        if action == "stop":
            self.stop()
        elif self.pending:
            self._set_state("recognizing", "Recognizing…")
        elif self.active:
            self._set_state("listening", "Listening…")
        elif not self.pending and not self.testing:
            self._set_state("idle", "Ready")
        return GLib.SOURCE_REMOVE

    def _recognition_failed(self, session_id: int, message: str) -> bool:
        if session_id != self.session_id:
            return GLib.SOURCE_REMOVE
        self.pending = max(0, self.pending - 1)
        self._set_state("error", message)
        return GLib.SOURCE_REMOVE

    def _capture_failed(self, session_id: int | None, message: str) -> bool:
        if session_id != self.session_id:
            return GLib.SOURCE_REMOVE
        self.active = False
        self.testing = False
        self.session_id += 1
        self._invalidate_partials()
        self.pending = 0
        if self.capture:
            self.capture.stop(flush=False)
            self.capture = None
        self._set_state("error", f"Microphone unavailable: {message}")
        return GLib.SOURCE_REMOVE

    def _no_speech(self, session_id: int | None) -> bool:
        if (
            session_id == self.session_id
            and self.active
            and not self.pending
            and self.state == "listening"
        ):
            self._set_state("no-speech", "No speech detected")
            GLib.timeout_add(1800, self._restore_listening_state, session_id)
        return GLib.SOURCE_REMOVE

    def _restore_listening_state(self, session_id: int | None) -> bool:
        if (
            session_id == self.session_id
            and self.active
            and self.state == "no-speech"
        ):
            self._set_state("listening", "Listening…")
        return GLib.SOURCE_REMOVE

    def _set_session_state(self, session_id: int, state: str, detail: str) -> bool:
        if session_id == self.session_id and self.active:
            self._set_state(state, detail)
        return GLib.SOURCE_REMOVE

    def _set_state(self, state: str, detail: str) -> bool:
        self.state, self.detail = state, detail
        self._emit("StateChanged", GLib.Variant("(ss)", (state, detail)))
        return GLib.SOURCE_REMOVE

    def _emit_level(self, level: float) -> bool:
        self._emit("LevelChanged", GLib.Variant("(d)", (float(level),)))
        return GLib.SOURCE_REMOVE

    def _emit(self, signal: str, parameters: GLib.Variant) -> None:
        if self.connection is not None:
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, signal, parameters)

    def _play_cue(self, _event: str) -> None:
        if not self.settings.get_boolean("audio-cues"):
            return
        try:
            Gio.Subprocess.new(
                [
                    "/usr/bin/pw-play",
                    "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
                ],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error:
            pass

    def _quit(self) -> bool:
        if self.shutting_down:
            return GLib.SOURCE_REMOVE
        self.shutting_down = True
        self._put_work(-1, "quit", 0, 0, b"")
        if self.connection and self.registration_id:
            self.connection.unregister_object(self.registration_id)
        if self.shell_watch_id:
            Gio.bus_unwatch_name(self.shell_watch_id)
            self.shell_watch_id = 0
        if self.owner_id:
            Gio.bus_unown_name(self.owner_id)
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def _name_lost(self, *_arguments) -> None:
        # A non-primary instance receives this callback without ever owning the
        # name.  Do not ask GLib to release a name it did not acquire.
        self.owner_id = 0
        if not self.shutting_down:
            self._quit()

    def _get_shell_owner(self) -> str:
        if self.connection is None:
            return ""
        try:
            result = self.connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                GLib.Variant("(s)", (SHELL_BUS_NAME,)),
                GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            return result.unpack()[0] if result is not None else ""
        except GLib.Error:
            return ""

    def _shell_name_appeared(
        self, _connection: Gio.DBusConnection, _name: str, owner: str
    ) -> None:
        self.shell_owner = owner

    def _shell_name_vanished(self, *_arguments) -> None:
        self.shell_owner = ""
        if not self.shutting_down:
            GLib.idle_add(self._quit)

    def _idle_check(self) -> bool:
        idle = not self.active and not self.testing and not self.pending
        if idle and time.monotonic() - self.last_activity >= 300:
            self._quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE


def main() -> int:
    try:
        return VoiceTypingService().run()
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"anduinos-whisper-framework: {error}", file=sys.stderr)
        return 1
