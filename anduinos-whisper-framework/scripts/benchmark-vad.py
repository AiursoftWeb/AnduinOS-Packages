#!/usr/bin/python3
"""Developer-only batch Silero comparison using the pinned private 1.8.3 ABI.

NOT a production binding or streaming implementation: upstream resets recurrent
state on every detect_speech call. This experiment evaluates whole public clips
before deciding whether a bounded native streaming interface is worthwhile.
"""
import argparse
from array import array
import ctypes as C
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import sys
import time
import wave

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture_probe', ROOT / 'scripts/benchmark-capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class ContextParams(C.Structure):
    _fields_ = [('n_threads', C.c_int), ('use_gpu', C.c_bool), ('gpu_device', C.c_int)]


class BatchVad:
    def __init__(self, library, model):
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if hashlib.sha256(model.read_bytes()).hexdigest() != '2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987':
            raise RuntimeError('Expected pinned upstream Silero v6.2.0 fixture')
        self.lib = C.CDLL(str(library))
        self.lib.whisper_version.restype = C.c_char_p
        if self.lib.whisper_version() != b'1.8.3':
            raise RuntimeError('Only the matching private 1.8.3 library is supported')
        logger = C.CFUNCTYPE(None, C.c_int, C.c_char_p, C.c_void_p)
        self.silent = logger(lambda *args: None)
        for name in ('whisper_log_set', 'ggml_log_set'):
            fn = getattr(self.lib, name)
            fn.argtypes = [logger, C.c_void_p]
            fn(self.silent, None)
        self.lib.ggml_backend_load_all.argtypes = []
        self.lib.ggml_backend_load_all.restype = None
        self.lib.ggml_backend_load_all()
        self.lib.whisper_vad_default_context_params.restype = ContextParams
        self.lib.whisper_vad_init_from_file_with_params.argtypes = [C.c_char_p, ContextParams]
        self.lib.whisper_vad_init_from_file_with_params.restype = C.c_void_p
        self.lib.whisper_vad_detect_speech.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_int]
        self.lib.whisper_vad_detect_speech.restype = C.c_bool
        self.lib.whisper_vad_n_probs.argtypes = [C.c_void_p]
        self.lib.whisper_vad_n_probs.restype = C.c_int
        self.lib.whisper_vad_probs.argtypes = [C.c_void_p]
        self.lib.whisper_vad_probs.restype = C.POINTER(C.c_float)
        self.lib.whisper_vad_free.argtypes = [C.c_void_p]
        params = self.lib.whisper_vad_default_context_params()
        params.n_threads, params.use_gpu = 1, False
        self.ctx = self.lib.whisper_vad_init_from_file_with_params(str(model).encode(), params)
        if not self.ctx:
            raise RuntimeError('VAD model could not be loaded')

    def probabilities(self, pcm):
        values = array('h'); values.frombytes(pcm)
        if sys.byteorder != 'little':
            values.byteswap()
        floats = (C.c_float * len(values))(*(x / 32768 for x in values))
        if not self.lib.whisper_vad_detect_speech(self.ctx, floats, len(values)):
            raise RuntimeError('VAD inference failed')
        count = self.lib.whisper_vad_n_probs(self.ctx)
        if count != (len(values) + 511) // 512:
            raise RuntimeError('Unexpected VAD frame count')
        return list(self.lib.whisper_vad_probs(self.ctx)[:count])

    def close(self):
        if self.ctx:
            self.lib.whisper_vad_free(self.ctx)
            self.ctx = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--vad-model', type=Path, required=True)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--model', type=Path, required=True)
    args = parser.parse_args()
    vad = BatchVad(args.library, args.vad_model)
    directory = ROOT / 'data/benchmark'
    manifest = json.loads((directory / 'manifest.json').read_text())
    engines, results = {}, []
    try:
        for sample in manifest['samples']:
            path = directory / sample['file']
            if hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']:
                raise RuntimeError('Corpus checksum mismatch')
            with wave.open(str(path)) as audio:
                pcm = audio.readframes(audio.getnframes())
            language = sample['language']
            if language not in engines:
                engines[language] = capture.ResidentEngine(args.model, language, 4,
                    backend='cpu', executable=args.worker)
            engine = engines[language]
            expected = capture.scoring.units(sample['text'], language)
            level = capture.rms_dbfs(pcm)
            for label, dbfs in [('source', level), ('normal', -26), ('quiet', -46)]:
                for snr in (None, 20, 10):
                    data = capture.stimulus(pcm, snr, 10 ** ((dbfs - level) / 20))
                    raw = data[32000:32000+len(pcm)]
                    baseline = capture.scoring.edit_distance(expected,
                        capture.scoring.units(engine.transcribe(raw), language))
                    started = time.monotonic()
                    probabilities = vad.probabilities(data)
                    vad_ms = (time.monotonic() - started) * 1000
                    chunks, events = [], []
                    frontend = capture.AudioCapture('', chunks.append, lambda _: None,
                        lambda _: None, lambda _: None, lambda: None)
                    def delivered(chunk, metrics):
                        chunks.append(chunk)
                        events.append({**metrics, 'submitted_audio_seconds': frontend._audio_seconds,
                                       'chunk_seconds': len(chunk) / 32000})
                    frontend.on_chunk_metrics = delivered
                    for i, probability in enumerate(probabilities):
                        frontend._consume(data[i*1024:(i+1)*1024], voiced=probability >= 0.5)
                    text = ' '.join(engine.transcribe(chunk) for chunk in chunks)
                    errors = capture.scoring.edit_distance(expected, capture.scoring.units(text, language))
                    results.append({'sample': sample['file'], 'level': label, 'snr_db': snr,
                        'input_rms_dbfs': capture.rms_dbfs(raw), 'vad_ms': vad_ms,
                        'raw_errors': baseline, 'frontend_errors': errors, 'units': len(expected),
                        'chunks': len(chunks), 'endpoint_passed': bool(chunks) and not frontend._speaking,
                        'events': events})
                    print(json.dumps({k: results[-1][k] for k in
                        ('sample', 'level', 'snr_db', 'chunks', 'raw_errors', 'frontend_errors')}), file=sys.stderr)
        # Stationary noise checks have no recognition step: false triggers are
        # failures regardless of whether the ASR happens to output no text.
        noise_checks = []
        for sigma in (50, 580, 3000):
            rng = capture.random.Random(42)
            noise = array('h', (max(-32768, min(32767, round(rng.gauss(0, sigma))))
                               for _ in range(15 * 16000))).tobytes()
            probabilities = vad.probabilities(noise)
            noise_checks.append({'sigma': sigma, 'voiced_frames': sum(p >= 0.5 for p in probabilities)})
    finally:
        vad.close()
        for engine in engines.values():
            engine.close()
    print(json.dumps({'experimental_batch_only': True, 'microphone_opened': False,
        'results': results, 'stationary_noise': noise_checks}, indent=2))


if __name__ == '__main__':
    main()
