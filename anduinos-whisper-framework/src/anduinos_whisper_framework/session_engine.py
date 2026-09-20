"""One resident engine lifecycle with a bounded GPU-to-CPU retry."""
import os
from pathlib import Path
import time

from .errors import RecognitionCancelled, RecognitionError, ResidentUnavailable
from .resident import ResidentEngine


class SessionEngine:
    def __init__(self, resident_factory=ResidentEngine,
                 idle_seconds=180, clock=time.monotonic):
        self.resident_factory = resident_factory
        self.idle_seconds, self.clock = idle_seconds, clock
        self.engine = None
        self.key = None
        self.gpu_failed = False
        self.last_used = clock()
        self.last_metrics = {}

    @staticmethod
    def _configuration(model, language, backend, threads):
        model = Path(model)
        stat = model.stat()
        threads = threads or min(4, max(1, len(os.sched_getaffinity(0))))
        key = (str(model), stat.st_size, stat.st_mtime_ns, language, backend, threads)
        return model, threads, key

    def prepare(self, model, language, fixture, cancel=None, backend="cpu", threads=0):
        """Warm only a new/reloaded worker; report whether inference was needed."""
        if cancel is not None and cancel.is_set():
            raise RecognitionCancelled()
        _model, _threads, key = self._configuration(model, language, backend, threads)
        if self.engine is not None and key == self.key:
            if self.engine.is_running():
                self.last_metrics = {}
                self.last_used = self.clock()
                return False
            self.close()
        self.transcribe(model, language, fixture, cancel, backend, threads)
        return True

    def transcribe(self, model, language, pcm, cancel=None, backend="cpu", threads=0):
        model, threads, key = self._configuration(model, language, backend, threads)
        if key != self.key:
            self.close()
            self.key = key
            self.gpu_failed = False
        effective = "cpu" if self.gpu_failed else backend
        self.last_metrics = {}
        try:
            if self.engine is None:
                self.engine = self._create(model, language, threads, effective)
            try:
                text = self.engine.transcribe(pcm, cancel)
            except ResidentUnavailable:
                # Missing/incompatible required package is not a GPU failure.
                # Never silently switch to the per-phrase CLI slow path.
                self.close()
                raise
            except RecognitionError:
                self.close()
                # Only one GPU -> CPU retry. Never loop or change the model.
                if effective != "gpu":
                    raise
                self.gpu_failed = True
                self.engine = self._create(model, language, threads, "cpu")
                try:
                    text = self.engine.transcribe(pcm, cancel)
                except RecognitionError:
                    self.close()
                    raise
            self.last_metrics = dict(self.engine.last_metrics)
            if self.gpu_failed:
                self.last_metrics["fallback"] = "gpu_failed"
            return text
        finally:
            self.last_used = self.clock()

    def _create(self, model, language, threads, backend):
        return self.resident_factory(model, language, threads, backend=backend)

    def release_if_idle(self):
        if self.clock() - self.last_used >= self.idle_seconds:
            self.close()

    def invalidate(self):
        """Discard loaded libraries and failure memory after explicit revalidation.

        Ordinary idle release deliberately retains GPU failure memory so that a
        broken device is not retried on every phrase. A successful new benchmark
        (including after driver changes) or a manual retry needs a fresh start.
        Call only from the recognition worker that owns this engine.
        """
        self.close()
        self.key = None
        self.gpu_failed = False
        self.last_metrics = {}

    def close(self):
        if self.engine is not None:
            close = getattr(self.engine, "close", None)
            if close:
                close()
            self.engine = None
