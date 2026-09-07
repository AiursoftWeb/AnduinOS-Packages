#!/usr/bin/python3
"""Continuous private-pipe VAD regression using only pinned public audio.

Runs faster than real time; simulated audio duration is not wall-clock soak time.
Checks persistent state, bounded resources, deterministic replay and cleanup.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time
import wave

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from anduinos_whisper_framework.vad import VadEngine

spec = importlib.util.spec_from_file_location('resident_stress', ROOT / 'scripts/stress-resident.py')
resident_stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resident_stress)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', required=True)
    parser.add_argument('--vad-model', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=30000)
    args = parser.parse_args()
    if args.frames < 2000:
        parser.error('Require at least 2000 frames after warm-up')
    directory = ROOT / 'data/benchmark'
    data = bytearray()
    for sample in json.loads((directory / 'manifest.json').read_text())['samples']:
        path = directory / sample['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']:
            raise RuntimeError('Corpus checksum mismatch')
        with wave.open(str(path)) as audio:
            data.extend(audio.readframes(audio.getnframes()))
        data.extend(b'\0' * 64000)
    frames = [bytes(data[i:i+1024]).ljust(1024, b'\0') for i in range(0, len(data), 1024)]
    expected, elapsed, observed = [], [], []
    started = time.monotonic()
    pids = []
    for replay in range(2):
        with VadEngine(args.vad_model, args.worker) as engine:
            engine.start()
            pid = engine.process.pid
            pids.append(pid)
            baseline = None
            for index in range(args.frames):
                before = time.monotonic()
                probability = engine.classify(frames[index % len(frames)])
                elapsed.append((time.monotonic() - before) * 1000)
                if replay:
                    if probability != expected[index]:
                        raise RuntimeError('Recurrent probability sequence changed on replay')
                else:
                    expected.append(probability)
                if engine.process.pid != pid:
                    raise RuntimeError('Worker restarted during continuous capture')
                if (index + 1) % 1000 == 0:
                    current = resident_stress.resources(pid)
                    if baseline is None:
                        baseline = current
                    if (current['fds'] != baseline['fds'] or current['threads'] != baseline['threads']
                            or current['rss_mib'] - baseline['rss_mib'] > 16):
                        raise RuntimeError('VAD resource growth exceeded its post-warm-up bound')
                    observed.append({**current, 'rss_growth_mib': current['rss_mib'] - baseline['rss_mib']})
                    if (index + 1) % 10000 == 0:
                        print(json.dumps({'replay': replay, 'frames': index+1, 'resources': current}), file=sys.stderr, flush=True)
        if Path(f'/proc/{pid}').exists():
            raise RuntimeError('VAD child was not reaped')
    if not any(p >= 0.5 for p in expected) or not any(p < 0.5 for p in expected):
        raise RuntimeError('Both speech and non-speech must be exercised')
    print(json.dumps({'passed': True, 'frames_per_replay': args.frames, 'replays': 2,
        'simulated_audio_seconds_per_replay': args.frames * 0.032,
        'elapsed_seconds': time.monotonic() - started,
        'maximum_rss_growth_mib': max(row['rss_growth_mib'] for row in observed),
        'maximum_rss_mib': max(row['rss_mib'] for row in observed),
        'worker_threads': sorted(set(row['threads'] for row in observed)),
        'frame_ms': {'median': statistics.median(elapsed), 'p95': sorted(elapsed)[int(len(elapsed)*0.95)]},
        'children_reaped': len(pids)}, indent=2))


if __name__ == '__main__':
    main()
