"""Single-owner private pipe transport to the isolated native model process.

There are no TCP endpoints and no audio files. Cancellation first asks the
engine to abort cooperatively, then kills/reaps a nonresponsive worker. Callers
must serialize transcribe/close, as the daemon's recognition thread does.
"""
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import time

from .chinese import normalize_chinese_script, whisper_language
from .commands import clean_transcript
from .diagnostics import sanitize
from .errors import RecognitionCancelled, RecognitionError, RecognitionTimeout, ResidentUnavailable


class WorkerTransport:
    """Bounded inherited-pipe transport shared by isolated ASR and VAD workers."""
    def __init__(self, timeout):
        self.timeout = timeout
        self.process = None
        self._response = bytearray()

    def _exchange(self, request=b"", cancel=None):
        process = self.process
        deadline = time.monotonic() + self.timeout
        remaining = memoryview(request)
        abort_deadline = None
        while True:
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                # Incomplete input cannot be reused: the worker still expects
                # the rest of that PCM frame. Startup is not cancellable in C.
                if remaining or not request:
                    self.close()
                    raise RecognitionCancelled()
                if abort_deadline is None:
                    if process.poll() is None:
                        process.send_signal(signal.SIGUSR1)
                    abort_deadline = now + 0.5
                if now >= abort_deadline:
                    self.close()
                    raise RecognitionCancelled()
            if now >= deadline:
                self.close()
                raise RecognitionTimeout("Speech recognition timed out")
            if b"\n" in self._response:
                line, _, tail = self._response.partition(b"\n")
                self._response = bytearray(tail)
                try:
                    result = json.loads(line)
                    if not isinstance(result, dict):
                        raise ValueError()
                    if "metrics" in result and not isinstance(result["metrics"], dict):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    self.close()
                    raise RecognitionError("Invalid recognition worker response") from None
                if abort_deadline is not None:
                    # If success raced with SIGUSR1, the signal may be pending
                    # for the next request. Only explicit abort acknowledgement
                    # permits reuse; otherwise restart to avoid a stale signal.
                    if result.get("status") != "cancelled":
                        self.close()
                    raise RecognitionCancelled()
                return result
            readable, writable, _ = select.select(
                [process.stdout], [process.stdin] if remaining else [], [], 0.02)
            if writable:
                try:
                    sent = os.write(process.stdin.fileno(), remaining[:16384])
                    remaining = remaining[sent:]
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    self.close()
                    raise RecognitionError("Recognition worker exited") from None
            if readable:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    self.close()
                    raise RecognitionError("Recognition worker exited")
                self._response.extend(chunk)
                if len(self._response) > 1_048_576:
                    self.close()
                    raise RecognitionError("Recognition worker response is too large")

    def close(self):
        process, self.process = self.process, None
        self._response.clear()
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ResidentEngine(WorkerTransport):
    def __init__(self, model: Path, language="auto", threads=4, backend="cpu", beam=5,
                 executable="/usr/libexec/anduinos-whisper-worker", timeout=120, no_fallback=False,
                 no_flash_attn=False):
        if backend not in {"cpu", "gpu"} or not 1 <= threads <= 256 or not 1 <= beam <= 5:
            raise ValueError("Invalid recognition configuration")
        super().__init__(timeout)
        self.model = Path(model)
        self.output_language = language
        self.language = whisper_language(language)
        self.threads, self.backend, self.beam = threads, backend, beam
        self.executable = str(executable)
        self.no_fallback = no_fallback
        self.no_flash_attn = no_flash_attn
        self.last_metrics = {}
        self.ready_metrics = {}

    def transcribe(self, pcm, cancel=None):
        self.last_metrics = {}
        if len(pcm) < 16000:
            return ""
        if len(pcm) % 2 or len(pcm) > 60 * 32000:
            raise RecognitionError("Invalid speech audio length")
        if cancel is not None and cancel.is_set():
            raise RecognitionCancelled()
        initialization = {}
        if self.process is None:
            if not self.model.is_file():
                raise RecognitionError("The selected speech model is not installed")
            try:
                command = [
                    self.executable, str(self.model), self.language,
                    str(self.threads), self.backend, str(self.beam),
                ]
                if self.no_fallback:
                    command.append("--no-fallback")
                if self.no_flash_attn:
                    command.append("--no-flash-attn")
                self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, bufsize=0)
            except OSError:
                raise ResidentUnavailable("The recognition worker could not be started") from None
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            ready = self._exchange(cancel=cancel)
            if ready.get("status") != "ready":
                self.close()
                raise ResidentUnavailable("The recognition library is missing or unsupported; reinstall Voice Typing")
            self.ready_metrics = sanitize(ready.get("metrics", {}))
            initialization = self.ready_metrics
        result = self._exchange(struct.pack("<I", len(pcm)) + pcm, cancel)
        self.last_metrics = {**initialization, **sanitize(result.get("metrics", {}))}
        if result.get("status") == "cancelled":
            raise RecognitionCancelled()
        if result.get("status") != "success" or not isinstance(result.get("text"), str):
            self.close()
            raise RecognitionError("Speech recognition failed")
        return normalize_chinese_script(clean_transcript(result["text"]), self.output_language)
