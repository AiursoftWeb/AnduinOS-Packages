#!/usr/bin/python3
"""Exercise real DSP/VAD/endpoint processing with public fixtures, never a mic.

Reports frontend changes relative to raw PCM recognition, not a guarantee for
physical microphones. Noise extends through the leading/trailing pauses. Audio
timeline latency is distinct from inference wall time. No transcript is exported.
"""
import argparse
from array import array
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'src'))
from anduinos_whisper_framework.audio import AudioCapture, Gst
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.vad import VadEngine, VAD_MODEL

spec = importlib.util.spec_from_file_location('corpus_scoring', ROOT / 'tests/benchmarks/benchmark-corpus.py')
scoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)


def stimulus(pcm, snr=None, gain=1.0):
    """One second room tone, known speech, three seconds same room tone."""
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    rms = math.sqrt(sum(x*x for x in samples) / len(samples))
    sigma = 0 if snr is None else rms / 10 ** (snr / 20)
    rng = random.Random(42)
    signal = [0] * 16000 + list(samples) + [0] * 48000
    result = array('h', (max(-32768, min(32767, round(gain * (x + rng.gauss(0, sigma)))))
                         for x in signal))
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


def rms_dbfs(pcm):
    values = array('h')
    values.frombytes(pcm)
    if sys.byteorder != 'little':
        values.byteswap()
    rms = math.sqrt(sum(x*x for x in values) / len(values))
    return 20 * math.log10(max(rms, 1e-9) / 32768)


def capture_fixture(pcm, reduction, vad):
    chunks, events, errors = [], [], []
    capture = AudioCapture('', chunks.append, lambda _: None, lambda _: None,
                           errors.append, lambda: None, noise_reduction=reduction, vad=vad)
    def delivered(data, metrics):
        chunks.append(data)
        events.append({**metrics, 'submitted_audio_seconds': capture._audio_seconds,
                       'chunk_seconds': len(data) / 32000})
    capture.on_chunk_metrics = delivered
    pipeline = Gst.parse_launch('appsrc name=input format=time ! '
        'audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved ! '
        'webrtcdsp name=dsp ! appsink name=output emit-signals=true sync=false')
    AudioCapture.configure_processor(pipeline.get_by_name('dsp'), reduction)
    bus = pipeline.get_bus()
    if vad is None:
        raise ValueError('A prepared native VAD is required')
    pipeline.get_by_name('output').connect('new-sample', capture._new_sample)
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Fixture DSP pipeline failed to start')
        source = pipeline.get_by_name('input')
        # Feed the actual capture callback with DSP-processed 10 ms buffers.
        for offset in range(0, len(pcm), 320):
            data = pcm[offset:offset+320].ljust(320, b'\0')
            buffer = Gst.Buffer.new_allocate(None, len(data), None)
            buffer.fill(0, data)
            buffer.pts = offset * Gst.SECOND // 32000
            buffer.duration = Gst.SECOND // 100
            if source.emit('push-buffer', buffer) != Gst.FlowReturn.OK:
                raise RuntimeError('Fixture DSP rejected PCM')
        source.emit('end-of-stream')
        message = bus.timed_pop_filtered(20 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None or message.type != Gst.MessageType.EOS or errors:
            raise RuntimeError('Fixture DSP failed or timed out')
        still_listening = capture._speaking
        # Do not flush on EOS: that would disguise an endpoint failure.
        return chunks, events, still_listening
    finally:
        pipeline.set_state(Gst.State.NULL)
        bus.set_sync_handler(None)
        capture.stop(flush=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', default='/usr/libexec/anduinos-whisper-worker')
    parser.add_argument('--model', type=Path, default=Path('/usr/share/anduinos-whisper-framework/models/ggml-base.bin'))
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--vad-model', type=Path, default=VAD_MODEL)
    parser.add_argument('--normalize-dbfs', type=float,
                        help='Explicit test input RMS level; omitted preserves source level')
    args = parser.parse_args()
    directory = ROOT / 'data/benchmark'
    manifest = json.loads((directory / 'manifest.json').read_text())
    engines, results = {}, []
    endpoint_passed = True
    try:
        for sample in manifest['samples']:
            path = directory / sample['file']
            if hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']:
                raise RuntimeError('Public fixture checksum mismatch')
            with wave.open(str(path), 'rb') as audio:
                if (audio.getframerate(), audio.getsampwidth(), audio.getnchannels()) != (16000, 2, 1):
                    raise RuntimeError('Invalid fixture format')
                pcm = audio.readframes(audio.getnframes())
            language = sample['language']
            source_level = rms_dbfs(pcm)
            normalization = (1 if args.normalize_dbfs is None else
                             10 ** ((args.normalize_dbfs - source_level) / 20))
            if language not in engines:
                engines[language] = ResidentEngine(args.model, language, args.threads,
                    backend='cpu', executable=args.worker)
            engine = engines[language]
            expected = scoring.units(sample['text'], language)
            for condition, snr, gain in [('clean', None, 1), ('noise-20dB', 20, 1),
                                         ('noise-10dB', 10, 1), ('quiet-noise-20dB', 20, 0.1)]:
                data = stimulus(pcm, snr, gain * normalization)
                raw = data[32000:32000+len(pcm)]
                baseline = scoring.edit_distance(expected, scoring.units(engine.transcribe(raw), language))
                for reduction in (False, True):
                    vad = None
                    try:
                        vad = VadEngine(args.vad_model, args.worker)
                        vad.start()
                        chunks, events, still_listening = capture_fixture(data, reduction, vad)
                    finally:
                        if vad is not None:
                            vad.close()
                    started = time.monotonic()
                    text = ' '.join(engine.transcribe(chunk) for chunk in chunks)
                    elapsed = (time.monotonic() - started) * 1000
                    errors = scoring.edit_distance(expected, scoring.units(text, language))
                    endpoint_ok = bool(chunks) and not still_listening
                    endpoint_passed &= endpoint_ok
                    results.append({'sample': sample['file'], 'condition': condition,
                        'noise_reduction': reduction, 'endpoint_passed': endpoint_ok,
                        'chunks': len(chunks), 'events': events, 'raw_errors': baseline,
                        'source_rms_dbfs': round(source_level, 3),
                        'input_rms_dbfs': round(rms_dbfs(raw), 3),
                        'frontend_errors': errors, 'units': len(expected),
                        'inference_wall_ms': round(elapsed, 3)})
    finally:
        for engine in engines.values():
            engine.close()
    print(json.dumps({'schema_version': 1, 'microphone_opened': False,
        'backend': 'cpu', 'corpus_version': manifest['version'],
        'normalization_dbfs': args.normalize_dbfs,
        'detector': 'silero-streaming-pipe',
        'endpoint_passed': endpoint_passed,
        'accuracy_nonregression': all(r['frontend_errors'] <= r['raw_errors'] for r in results),
        'results': results}, indent=2))
    # Accuracy differences are reported for investigation; do not erase them by
    # calling DSP processing numerically equivalent to unprocessed inference.
    return 0 if endpoint_passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
