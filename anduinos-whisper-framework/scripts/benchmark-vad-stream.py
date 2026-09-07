#!/usr/bin/python3
"""Check experimental native streaming VAD against pinned upstream batch VAD.

All data is public/synthetic. No microphone, no recognition, no production
binding. A per-frame wall-time report describes only the current machine.
"""
from array import array
import argparse
import ctypes as C
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
import wave

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('batch_probe', ROOT / 'scripts/benchmark-vad.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)
from anduinos_whisper_framework.vad import VadEngine


class StreamVad:
    def __init__(self, lib, model):
        self.lib = lib
        lib.anduinos_vad_stream_open_v1.argtypes = [C.c_char_p]
        lib.anduinos_vad_stream_open_v1.restype = C.c_void_p
        lib.anduinos_vad_stream_close_v1.argtypes = [C.c_void_p]
        lib.anduinos_vad_stream_close_v1.restype = None
        lib.anduinos_vad_stream_process_v1.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_size_t, C.POINTER(C.c_float)]
        lib.anduinos_vad_stream_process_v1.restype = C.c_bool
        self.handle = lib.anduinos_vad_stream_open_v1(str(model).encode())
        if not self.handle:
            raise RuntimeError('Stream VAD init failed')

    def probabilities(self, pcm):
        result, timings = [], []
        for offset in range(0, len(pcm), 1024):
            samples = array('h'); samples.frombytes(pcm[offset:offset+1024].ljust(1024, b'\0'))
            if sys.byteorder != 'little':
                samples.byteswap()
            values = (C.c_float * 512)(*(x / 32768 for x in samples))
            probability = C.c_float()
            started = time.monotonic()
            if not self.lib.anduinos_vad_stream_process_v1(self.handle, values, 512, C.byref(probability)):
                raise RuntimeError('Stream VAD inference failed')
            timings.append((time.monotonic() - started) * 1000)
            result.append(probability.value)
        return result, timings

    def close(self):
        if self.handle:
            self.lib.anduinos_vad_stream_close_v1(self.handle)
            self.handle = None


class PipeVad:
    def __init__(self, worker, model):
        self.engine = VadEngine(model, worker)
        self.engine.start()

    def probabilities(self, pcm):
        result, timings = [], []
        for offset in range(0, len(pcm), 1024):
            data = pcm[offset:offset+1024].ljust(1024, b'\0')
            started = time.monotonic()
            result.append(self.engine.classify(data))
            timings.append((time.monotonic() - started) * 1000)
        return result, timings

    def close(self):
        self.engine.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--vad-model', type=Path, required=True)
    parser.add_argument('--worker', type=Path, help='Exercise the actual bounded private pipe instead of direct C calls')
    args = parser.parse_args()
    reference = batch.BatchVad(args.library, args.vad_model)
    directory = ROOT / 'data/benchmark'
    manifest = json.loads((directory / 'manifest.json').read_text())
    results, timings = [], []
    try:
        for sample in manifest['samples']:
            path = directory / sample['file']
            if batch.hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']:
                raise RuntimeError('Corpus checksum mismatch')
            with wave.open(str(path)) as audio:
                pcm = audio.readframes(audio.getnframes())
            source_level = batch.capture.rms_dbfs(pcm)
            for label, level in [('source', source_level), ('normal', -26), ('quiet', -46)]:
                for snr in (None, 20, 10):
                    data = batch.capture.stimulus(pcm, snr, 10 ** ((level-source_level)/20))
                    expected = reference.probabilities(data)
                    stream = (PipeVad(args.worker, args.vad_model) if args.worker else
                              StreamVad(reference.lib, args.vad_model))
                    try:
                        actual, elapsed = stream.probabilities(data)
                        # Invalid frame sizes must not read/write model state.
                        if not args.worker:
                            values = (C.c_float * 512)()
                            probability = C.c_float()
                            if reference.lib.anduinos_vad_stream_process_v1(stream.handle, values, 511, C.byref(probability)):
                                raise RuntimeError('Invalid frame accepted')
                    finally:
                        stream.close()
                    if len(actual) != len(expected):
                        raise RuntimeError('Frame count mismatch')
                    difference = max(abs(x-y) for x,y in zip(expected, actual))
                    results.append({'sample': sample['file'], 'level': label, 'snr_db': snr,
                        'frames': len(actual), 'maximum_probability_difference': difference})
                    timings.extend(elapsed)
        passed = all(r['maximum_probability_difference'] <= 1e-6 for r in results)
    finally:
        reference.close()
    print(json.dumps({'streaming_equivalent': passed, 'cases': results,
        'transport': 'private-pipe' if args.worker else 'direct-c',
        'frame_ms': {'median': statistics.median(timings),
                     'p95': sorted(timings)[int(len(timings)*0.95)], 'max': max(timings)},
        'frames': len(timings)}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
