"""Session D-Bus service for offline, phrase-by-phrase voice typing."""

from __future__ import annotations

import logging
import sys
import threading
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import APP_ID, INTERFACE, OBJECT_PATH
from .audio import AudioCapture
from .vad import VadEngine
from .commands import apply_voice_command, remove_punctuation
from .config import SETTINGS_SCHEMA, model_installed, model_path
from .errors import RecognitionCancelled, RecognitionTimeout
from .work_queue import RecognitionQueue
from .diagnostics import PerformanceHistory
from .session_engine import SessionEngine
from .tuning import AutomaticSelector


INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Start"/>
    <method name="Stop"/>
    <method name="Finish"/>
    <method name="Quit"/>
    <method name="StartTest"/>
    <method name="StopTest"/>
    <method name="ReportDelivery"><arg name="ticket" type="u" direction="in"/></method>
    <signal name="DeliveryTicket"><arg name="ticket" type="u"/></signal>
    <method name="GetDiagnostics">
      <arg name="report" type="s" direction="out"/>
    </method>
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
SHELL_CONTROL_METHODS = frozenset({"Start", "Stop", "Finish", "Quit", "ReportDelivery"})


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
        self.prepared_vad = None
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
        self.current_cancel = None
        self.current_kind = None
        self.finish_message = ""
        self.shutting_down = False
        self.last_activity = time.monotonic()
        self.audio_queue = RecognitionQueue()
        self.performance = PerformanceHistory()
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
                self.stop()  # Retain cancellation semantics for older clients.
            elif method == "Finish":
                self.finish()
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
            elif method == "GetDiagnostics":
                invocation.return_value(GLib.Variant("(s)", (self.performance.export_json(),)))
                return
            elif method == "ReportDelivery":
                self.performance.acknowledge_delivery(_parameters.unpack()[0], time.monotonic())
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
        # Starting again explicitly cancels any previous finishing session.
        self._cancel_work()
        self.finish_message = ""
        selected_model = self.settings.get_string("model") or "base"
        if not model_installed(selected_model):
            raise RuntimeError("The selected speech model is not installed")
        self.session_id += 1
        self._invalidate_partials()
        session_id = self.session_id
        self.pending = 0
        self.active = True
        # Snapshot settings for this session. Changing settings cannot silently
        # swap the model underneath audio that has already been accepted.
        self.session_config = {
            "model": selected_model,
            "language": self.settings.get_string("language") or "auto",
            "backend": self.settings.get_string("recognition-backend"),
            "threads": self.settings.get_uint("recognition-threads"),
            "generation": self.settings.get_uint("tuning-generation"),
        }
        self.capture = None
        self._set_state("preparing", "Preparing recognition…")
        self._put_work(0, "prepare", session_id, 0, b"", dict(self.session_config))

    def _preparation_state(self, session_id, state):
        if session_id == self.session_id and self.active and self.capture is None:
            detail = {
                "calibrating": "Measuring performance — microphone off",
                "preparing": "Preparing recognition…",
            }[state]
            self._set_state(state, detail)
        return GLib.SOURCE_REMOVE

    def _prepared(self, session_id, config, vad=None):
        with self.work_lock:
            if getattr(self, "prepared_vad", None) is vad:
                self.prepared_vad = None
        if session_id != self.session_id or not self.active:
            if vad is not None:
                vad.close()
            return GLib.SOURCE_REMOVE
        self.session_config = config
        try:
            self.capture = self._new_capture(session_id=session_id, vad=vad)
            self.capture.start()
        except Exception as error:
            if vad is not None:
                vad.close()
            return self._capture_failed(session_id, str(error))
        self._play_cue("audio-volume-change")
        self._set_state("listening", "Listening…")
        return GLib.SOURCE_REMOVE

    def stop(self) -> None:
        was_running = self.active or self.testing
        self.active = False
        self.testing = False
        self.session_id += 1
        self._invalidate_partials()
        self._cancel_work()
        self.pending = 0
        if self.capture:
            self.capture.stop(flush=False)
            self.capture = None
        if was_running:
            self._play_cue("audio-volume-change")
        self._set_state("idle", "Ready")

    def finish(self) -> None:
        """Stop recording, but retain this session until its final text arrives."""
        if not self.active:
            return
        if self.capture is None:
            # Finish during preparation means cancel, not a delayed mic start.
            self.stop()
            return
        capture, self.capture = self.capture, None
        if capture:
            # Flush before clearing active: _queue_audio must accept this phrase.
            capture.stop(flush=True)
        self.active = False
        self._invalidate_partials()
        self._play_cue("audio-volume-change")
        if self.pending:
            self._set_state("finishing", "Finishing recognition…")
        else:
            self._set_state("idle", "Ready")

    def _cancel_work(self) -> None:
        with self.work_lock:
            if self.current_cancel is not None:
                self.current_cancel.set()
            self.audio_queue.clear()
            prepared_vad, self.prepared_vad = getattr(self, "prepared_vad", None), None
        if prepared_vad is not None:
            prepared_vad.close()

    def start_test(self) -> None:
        if self.active:
            self.stop()
        if self.testing:
            return
        self.session_id += 1
        self._invalidate_partials()
        self._cancel_work()
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
        self._cancel_work()
        self.pending = 0
        if self.capture:
            self.capture.stop(flush=False)
            self.capture = None
        self._set_state("idle", "Ready")

    def _new_capture(
        self, testing: bool = False, session_id: int | None = None, vad=None
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
            noise_reduction=self.settings.get_boolean("noise-reduction"),
            on_chunk_metrics=None if testing else lambda audio, metrics: self._queue_audio(session_id, audio, metrics),
            vad=vad,
            detect_speech=not testing,
        )

    def _queue_audio(self, session_id: int | None, pcm: bytes, metrics=None) -> None:
        if session_id != self.session_id or not self.active:
            return
        generation = self._invalidate_partials()
        self.pending += 1
        if not self._put_work(0, "final", session_id, generation, pcm, metrics):
            self.pending -= 1
            GLib.idle_add(self._overloaded, session_id)
            return
        GLib.idle_add(
            self._set_session_state, session_id, "recognizing", "Recognizing…"
        )

    def _overloaded(self, session_id):
        if session_id != self.session_id:
            return GLib.SOURCE_REMOVE
        self.active = False
        if self.capture:
            self.capture.stop(flush=False)
            self.capture = None
        self._invalidate_partials()
        self.finish_message = "Recognition cannot keep up. The last phrase was not accepted; try a smaller model."
        self._set_state("finishing" if self.pending else "error", self.finish_message)
        return GLib.SOURCE_REMOVE

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
            if getattr(self, "current_kind", None) == "partial" and self.current_cancel is not None:
                self.current_cancel.set()
            if hasattr(self, "audio_queue"):
                self.audio_queue.clear(partial_only=True)
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
        metrics=None,
    ) -> bool:
        if session_id is None:
            return False
        with self.work_lock:
            sequence = self.work_sequence
            self.work_sequence += 1
        return self.audio_queue.put(
            (priority, sequence, kind, session_id, generation, pcm, time.monotonic(), metrics or {})
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
        session_engine = SessionEngine()
        selector = AutomaticSelector()
        while True:
            work = self.audio_queue.get(timeout=5)
            if work is None:
                session_engine.release_if_idle()
                continue
            _priority, _sequence, kind, session_id, generation, pcm, *timing = (
                work
            )
            dequeued = time.monotonic()
            try:
                if kind == "quit":
                    session_engine.close()
                    return
                if session_id != self.session_id:
                    continue
                if kind == "partial" and not self._partial_should_run(
                    session_id, generation
                ):
                    continue
                cancel = threading.Event()
                with self.work_lock:
                    self.current_cancel, self.current_kind = cancel, kind
                    # A final/cancel may have arrived between get() and this lock.
                    if session_id != self.session_id or (kind == "partial" and
                            (generation <= self.partial_floor or not self.active)):
                        continue
                if kind == "prepare":
                    config = dict(timing[1])
                    def begin_measurement():
                        # Do not benchmark a second model while the previous
                        # session holds RAM/VRAM. This can falsely reject an
                        # otherwise usable GPU on memory-constrained machines.
                        # Cached/manual selections never invoke this callback.
                        session_engine.close()
                        GLib.idle_add(self._preparation_state, session_id, "calibrating")
                    try:
                        choice = selector.select(
                            model_path(config["model"]), config["language"],
                            config["backend"], config["threads"], cancel=cancel,
                            generation=config["generation"],
                            on_measure=begin_measurement,
                        )
                    finally:
                        for measurement in selector.measurements:
                            self.performance.append({**measurement, "model": config["model"]})
                    if selector.status == "measured" or (
                            selector.status == "manual" and choice["backend"] == "gpu"
                            and session_engine.gpu_failed):
                        # A fresh measurement may follow a driver update even
                        # when the chosen model/backend/threads are unchanged.
                        # Drop stale libraries and the previous GPU failure memo.
                        session_engine.invalidate()
                    config.update(choice)
                    GLib.idle_add(self._preparation_state, session_id, "preparing")
                    fixture, _sample = selector.calibration(config["language"])
                    # Warm the selected persistent engine, not just the temporary
                    # probe engines. Discard fixture text; it must never reach Shell.
                    if session_engine.prepare(model_path(config["model"]), config["language"],
                                              fixture, cancel=cancel, **choice):
                        self.performance.append({**session_engine.last_metrics,
                                                 "kind": "benchmark", "status": "success"})
                    detector = VadEngine()
                    try:
                        detector.start(cancel)
                        self.performance.append({**detector.ready_metrics,
                            "kind": "benchmark", "engine": "vad", "backend": "cpu",
                            "threads": 1, "status": "success"})
                        with self.work_lock:
                            if cancel.is_set() or session_id != self.session_id or not self.active:
                                raise RecognitionCancelled()
                            self.prepared_vad = detector
                        GLib.idle_add(self._prepared, session_id, config, detector)
                    except Exception:
                        with self.work_lock:
                            if getattr(self, "prepared_vad", None) is detector:
                                self.prepared_vad = None
                        detector.close()
                        raise
                    continue
                config = getattr(self, "session_config", {})
                selected_model = config.get("model", self.settings.get_string("model") or "base")
                started = time.monotonic()
                text = session_engine.transcribe(
                    model_path(selected_model), config.get("language", self.settings.get_string("language") or "auto"),
                    pcm, cancel=cancel,
                    **({k: config[k] for k in ("backend", "threads")} if config else {}),
                )
                completed_at = time.monotonic()
                ticket = self.performance.append({
                    **session_engine.last_metrics, "kind": kind, "model": selected_model,
                    **(timing[1] if len(timing) > 1 else {}),
                    "status": "success",
                    **({"queue_ms": (dequeued - timing[0]) * 1000} if timing else {}),
                })
                logging.debug("Voice task %s: audio=%.2fs inference=%.2fs", kind,
                              len(pcm) / 32000, time.monotonic() - started)
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
                GLib.idle_add(self._recognition_finished, session_id, text, action, ticket, completed_at)
            except RecognitionCancelled:
                self.performance.append({"kind": kind, "status": "cancelled",
                                         "audio_ms": len(pcm) / 32})
                continue
            except Exception as error:
                self.performance.append({"kind": kind, "audio_ms": len(pcm) / 32,
                    "status": "timeout" if isinstance(error, RecognitionTimeout) else "error"})
                if kind in {"final", "prepare"}:
                    GLib.idle_add(self._recognition_failed, session_id, str(error))
                else:
                    logging.warning("Voice preview failed (%s)", type(error).__name__)
            finally:
                with self.work_lock:
                    self.current_cancel, self.current_kind = None, None

    def _partial_finished(
        self, session_id: int, generation: int, text: str
    ) -> bool:
        if self._partial_is_valid(session_id, generation) and text:
            self._emit("Transcript", GLib.Variant("(sb)", (text, False)))
        return GLib.SOURCE_REMOVE

    def _recognition_finished(
        self, session_id: int, text: str, action: str | None, ticket=None, completed_at=None
    ) -> bool:
        if session_id != self.session_id:
            return GLib.SOURCE_REMOVE
        self.pending = max(0, self.pending - 1)
        if text:
            if ticket is not None and completed_at is not None:
                self.performance.ready_for_delivery(ticket, completed_at)
                self._emit("DeliveryTicket", GLib.Variant("(u)", (ticket,)))
            self._emit("Transcript", GLib.Variant("(sb)", (text, True)))
        if action == "stop":
            self.stop()
        elif self.pending:
            self._set_state("recognizing" if self.active else "finishing", "Recognizing…")
        elif self.active:
            if text:
                self._set_state("listening", "Listening…")
            else:
                self._set_state("no-speech", "No speech detected")
                GLib.timeout_add(1800, self._restore_listening_state, session_id)
        elif not self.pending and not self.testing:
            if self.finish_message:
                self._set_state("error", self.finish_message)
            else:
                self._set_state("idle", "Ready")
        return GLib.SOURCE_REMOVE

    def _recognition_failed(self, session_id: int, message: str) -> bool:
        if session_id != self.session_id:
            return GLib.SOURCE_REMOVE
        self.pending = max(0, self.pending - 1)
        # Stop collecting more audio after a backend failure, but preserve
        # accepted final work instead of silently throwing away the queue.
        self.active = False
        if self.capture:
            self.capture.stop(flush=False)
            self.capture = None
        self._invalidate_partials()
        self.finish_message = message
        self._set_state("finishing" if self.pending else "error", message)
        return GLib.SOURCE_REMOVE

    def _capture_failed(self, session_id: int | None, message: str) -> bool:
        if session_id != self.session_id:
            return GLib.SOURCE_REMOVE
        self.active = False
        self.testing = False
        self.session_id += 1
        self._invalidate_partials()
        self._cancel_work()
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
        self.stop()
        self._put_work(-1, "quit", 0, 0, b"")
        # Let cancellation reap the resident native process before
        # exiting the interpreter (the worker itself is a daemon thread).
        self.worker.join(timeout=1.0)
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
