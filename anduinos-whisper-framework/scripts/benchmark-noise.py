#!/usr/bin/python3
"""Synthetic non-speech false-trigger checks through actual DSP and private VAD.

These are reproducible noise shapes, not recordings of real fans or keyboards.
No microphone is opened and no recognition/model transcript is generated.
"""
import argparse
from array import array
import importlib.util
import json
import math
from pathlib import Path
import random
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture_probe', ROOT / 'scripts/benchmark-capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def noise(shape, dbfs, seconds=15):
    rng = random.Random(42)
    values = []
    filtered = 0.0
    impulse_left = 0
    next_impulse = 1600
    for index in range(int(seconds * 16000)):
        if shape == 'hum':
            value = math.sin(2 * math.pi * 50 * index / 16000) + 0.4 * math.sin(2 * math.pi * 100 * index / 16000)
        elif shape == 'fan-like':
            filtered = 0.98 * filtered + 0.02 * rng.gauss(0, 1)
            value = filtered + 0.03 * rng.gauss(0, 1)
        elif shape == 'tapping':
            if index == next_impulse:
                impulse_left = 800
                next_impulse += rng.randint(2400, 8000)
            value = rng.gauss(0, 1) * math.exp(-(800-impulse_left)/128) if impulse_left else 0
            impulse_left = max(0, impulse_left - 1)
        else:
            raise ValueError('Unknown noise shape')
        values.append(value)
    rms = math.sqrt(sum(x*x for x in values) / len(values))
    scale = 32768 * 10 ** (dbfs/20) / rms
    result = array('h', (max(-32768, min(32767, round(x * scale))) for x in values))
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--vad-model', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for shape in ('hum', 'fan-like', 'tapping'):
        for dbfs in (-50, -35, -20):
            pcm = noise(shape, dbfs)
            for reduction in (False, True):
                with capture.VadEngine(args.vad_model, args.worker) as vad:
                    vad.start()
                    chunks, events, active = capture.capture_fixture(pcm, reduction, vad)
                rows.append({'shape': shape, 'requested_rms_dbfs': dbfs,
                    'actual_rms_dbfs': capture.rms_dbfs(pcm), 'noise_reduction': reduction,
                    'false_triggers': len(chunks), 'active_at_end': active})
    passed = all(not r['false_triggers'] and not r['active_at_end'] for r in rows)
    print(json.dumps({'passed': passed, 'microphone_opened': False,
                      'synthetic_shapes_only': True, 'cases': rows}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
