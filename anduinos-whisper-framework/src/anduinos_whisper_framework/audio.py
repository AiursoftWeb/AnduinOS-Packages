"""PipeWire/GStreamer microphone capture with phrase-oriented VAD."""

from __future__ import annotations

from array import array
from collections import deque
import math
import threading
import time
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import Gst, GstApp  # noqa: E402,F401


Gst.init(None)


def input_devices() -> list[tuple[str, str, bool]]:
    """Return stable PipeWire node names, display labels, and default state."""

    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Audio/Source", None)
    if not monitor.start():
        return []
    result: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    try:
        for device in monitor.get_devices():
            properties = device.get_properties()
            if properties is None:
                continue
            media_class = properties.get_string("media.class") or ""
            device_class = properties.get_string("device.class") or ""
            node_name = properties.get_string("node.name") or ""
            if media_class != "Audio/Source" or device_class == "monitor":
                continue
            if not node_name or node_name in seen:
                continue
            seen.add(node_name)
            is_default = False
            if properties.has_field("is-default"):
                is_default = bool(properties.get_value("is-default"))
            result.append((node_name, device.get_display_name(), is_default))
    finally:
        monitor.stop()
    return sorted(result, key=lambda item: (not item[2], item[1].casefold()))


class AudioCapture:
    """Capture 16 kHz mono audio and split it after short silences."""

    RATE = 16_000
    BYTES_PER_SECOND = RATE * 2

    def __init__(
        self,
        microphone: str,
        on_chunk: Callable[[bytes], None],
        on_partial: Callable[[bytes], None],
        on_level: Callable[[float], None],
        on_error: Callable[[str], None],
        on_no_speech: Callable[[], None],
        silence_threshold: float = -42.0,
        silence_seconds: float = 0.8,
        max_phrase_seconds: float = 12.0,
        partial_interval: float = 1.0,
    ):
        self.microphone = microphone
        self.on_chunk = on_chunk
        self.on_partial = on_partial
        self.on_level = on_level
        self.on_error = on_error
        self.on_no_speech = on_no_speech
        self.silence_threshold = silence_threshold
        self.silence_seconds = silence_seconds
        self.max_phrase_bytes = int(max_phrase_seconds * self.BYTES_PER_SECOND)
        self.partial_interval = partial_interval
        self._pipeline: Gst.Pipeline | None = None
        self._phrase = bytearray()
        self._pre_roll: deque[bytes] = deque()
        self._pre_roll_size = 0
        self._speaking = False
        self._last_voice = 0.0
        self._last_partial = 0.0
        self._last_speech_notice = time.monotonic()
        self._last_level_update = 0.0
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._pipeline is not None:
            return
        pipeline = Gst.Pipeline.new("anduinos-voice-capture")
        source = Gst.ElementFactory.make("pipewiresrc", "microphone")
        convert = Gst.ElementFactory.make("audioconvert", "convert")
        resample = Gst.ElementFactory.make("audioresample", "resample")
        caps = Gst.ElementFactory.make("capsfilter", "format")
        sink = Gst.ElementFactory.make("appsink", "samples")
        if not all((pipeline, source, convert, resample, caps, sink)):
            raise RuntimeError("Required PipeWire/GStreamer audio plugins are unavailable")
        if self.microphone:
            source.set_property("target-object", self.microphone)
        caps.set_property(
            "caps",
            Gst.Caps.from_string(
                "audio/x-raw,format=S16LE,layout=interleaved,rate=16000,channels=1"
            ),
        )
        sink.set_property("emit-signals", True)
        sink.set_property("sync", False)
        sink.set_property("max-buffers", 32)
        sink.set_property("drop", True)
        sink.connect("new-sample", self._new_sample)
        for element in (source, convert, resample, caps, sink):
            pipeline.add(element)
        linked = (
            source.link(convert)
            and convert.link(resample)
            and resample.link(caps)
            and caps.link(sink)
        )
        if not linked:
            raise RuntimeError("Could not build the microphone capture pipeline")

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._pipeline_error)
        change = pipeline.set_state(Gst.State.PLAYING)
        if change == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("The selected microphone could not be opened")
        self._pipeline = pipeline
        self._last_speech_notice = time.monotonic()

    def stop(self, flush: bool = True) -> None:
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        chunk = b""
        with self._lock:
            if flush and self._speaking:
                chunk = bytes(self._phrase)
            self._reset_phrase()
        if len(chunk) >= self.BYTES_PER_SECOND // 2:
            self.on_chunk(chunk)

    def _new_sample(self, sink: Gst.Element) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        success, mapped = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR
        try:
            data = bytes(mapped.data)
        finally:
            buffer.unmap(mapped)
        self._consume(data)
        return Gst.FlowReturn.OK

    def _consume(self, data: bytes) -> None:
        samples = array("h")
        samples.frombytes(data)
        if not samples:
            return
        stride = max(1, len(samples) // 256)
        sampled = samples[::stride]
        mean_square = sum(value * value for value in sampled) / len(sampled)
        db = 20.0 * math.log10(max(1.0, math.sqrt(mean_square)) / 32768.0)
        level = max(0.0, min(1.0, (db + 60.0) / 55.0))
        now = time.monotonic()
        if now - self._last_level_update >= 0.08:
            self._last_level_update = now
            self.on_level(level)
        completed = b""
        partial = b""
        notify_no_speech = False
        with self._lock:
            if not self._speaking:
                self._pre_roll.append(data)
                self._pre_roll_size += len(data)
                limit = int(0.35 * self.BYTES_PER_SECOND)
                while self._pre_roll_size > limit and self._pre_roll:
                    self._pre_roll_size -= len(self._pre_roll.popleft())
            if db >= self.silence_threshold:
                if not self._speaking:
                    self._speaking = True
                    self._last_partial = now
                    self._phrase.extend(b"".join(self._pre_roll))
                    self._pre_roll.clear()
                    self._pre_roll_size = 0
                else:
                    self._phrase.extend(data)
                self._last_voice = now
                self._last_speech_notice = now
            elif self._speaking:
                self._phrase.extend(data)

            phrase_complete = self._speaking and (
                now - self._last_voice >= self.silence_seconds
                or len(self._phrase) >= self.max_phrase_bytes
            )
            if phrase_complete:
                completed = bytes(self._phrase)
                self._reset_phrase()
            elif (
                self._speaking
                and now - self._last_partial >= self.partial_interval
                and len(self._phrase) >= self.BYTES_PER_SECOND // 2
            ):
                partial = bytes(self._phrase)
                self._last_partial = now
            elif not self._speaking and now - self._last_speech_notice >= 8.0:
                self._last_speech_notice = now
                notify_no_speech = True
        if completed:
            self.on_chunk(completed)
        elif partial:
            self.on_partial(partial)
        if notify_no_speech:
            self.on_no_speech()

    def _reset_phrase(self) -> None:
        self._phrase.clear()
        self._pre_roll.clear()
        self._pre_roll_size = 0
        self._speaking = False
        self._last_voice = 0.0
        self._last_partial = 0.0

    def _pipeline_error(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        error, _debug = message.parse_error()
        self.on_error(error.message)
