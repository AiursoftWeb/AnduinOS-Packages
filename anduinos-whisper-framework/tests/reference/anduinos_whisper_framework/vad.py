"""Single-owner streaming VAD client; native code stays in a private process.

Frames are exactly 32 ms, mono S16LE at 16 kHz. Never silently restart recurrent
state mid-capture: a transport failure ends capture and the next session starts
fresh. No audio files, model downloads, sockets or transcript output.
"""
import math
import os
from pathlib import Path
import struct
import subprocess

from .diagnostics import sanitize
from .errors import RecognitionCancelled, RecognitionError, ResidentUnavailable
from .resident import WorkerTransport

VAD_MODEL = Path('/usr/share/anduinos-whisper-framework/models/ggml-silero-v6.2.0.bin')


class VadEngine(WorkerTransport):
    FRAME_BYTES = 1024

    def __init__(self, model=VAD_MODEL, executable='/usr/libexec/anduinos-whisper-worker',
                 timeout=1.0):
        super().__init__(timeout)
        self.model, self.executable = Path(model), str(executable)
        self.ready_metrics = {}
        self.started = False

    def start(self, cancel=None):
        if self.process is not None:
            return
        if cancel is not None and cancel.is_set():
            raise RecognitionCancelled()
        if not self.model.is_file():
            raise ResidentUnavailable('The speech detection model is not installed')
        try:
            self.process = subprocess.Popen([self.executable, '--vad', str(self.model)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0)
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
            ready = self._exchange(cancel=cancel)
            if ready.get('status') != 'ready' or ready.get('mode') != 'vad':
                raise ResidentUnavailable('The speech detection worker is missing or unsupported')
            self.ready_metrics = sanitize(ready.get('metrics', {}))
            self.started = True
        except OSError:
            self.close()
            raise ResidentUnavailable('The speech detection worker could not be started') from None
        except Exception:
            self.close()
            raise

    def classify(self, pcm, cancel=None):
        if len(pcm) != self.FRAME_BYTES:
            raise ValueError('Speech detection expects exactly 32 ms of S16LE audio')
        if self.process is None or not self.started:
            raise RecognitionError('Speech detection is not running')
        try:
            result = self._exchange(struct.pack('<I', len(pcm)) + pcm, cancel)
            probability = result.get('probability')
            if (result.get('status') != 'success' or type(probability) not in (float, int)
                    or not math.isfinite(probability) or not 0 <= probability <= 1):
                raise RecognitionError('Invalid speech detection response')
            return float(probability)
        except Exception:
            self.close()
            raise

    def close(self):
        self.started = False
        super().close()
