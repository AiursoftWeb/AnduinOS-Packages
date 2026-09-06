#!/usr/bin/env python3
"""Offline endpoint benchmark with reproducible noise; never opens a microphone.

Use a consented/public 16 kHz mono PCM16 WAV. --transcribe is optional and local.
"""
import argparse
from array import array
import json
from pathlib import Path
import random
import sys
import time
import wave

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from anduinos_whisper_framework.audio import AudioCapture, Gst
from anduinos_whisper_framework.config import model_path
from anduinos_whisper_framework.engine import WhisperEngine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav', type=Path)
    parser.add_argument('--noise-dbfs', type=float, default=-35)
    parser.add_argument('--gain', type=float, default=1)
    parser.add_argument('--noise-reduction', action='store_true')
    parser.add_argument('--transcribe', action='store_true')
    parser.add_argument('--language', default='en')
    args = parser.parse_args()
    if not (-100 <= args.noise_dbfs <= -10) or not (0 < args.gain <= 4):
        parser.error('Use noise in [-100, -10] dBFS and gain in (0, 4]')
    with wave.open(str(args.wav)) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            parser.error('Expected a 16 kHz mono PCM16 WAV')
        if audio.getnframes() > 16000*120:
            parser.error('Use at most two minutes of audio per benchmark')
        speech = array('h', audio.readframes(audio.getnframes()))
    rng = random.Random(42)
    sigma = 32768 * 10 ** (args.noise_dbfs / 20)
    samples = [0]*16000 + [s*args.gain for s in speech] + [0]*32000
    pcm = array('h', [int(max(-32768, min(32767, value + rng.gauss(0, sigma))))
                      for value in samples])
    chunks, times = [], []
    capture = None

    def completed(data):
        chunks.append(data)
        times.append(round(capture._audio_seconds, 3))

    capture = AudioCapture('', completed, lambda _: None, lambda _: None,
                           lambda error: None, lambda: None,
                           noise_reduction=args.noise_reduction)
    pipeline = Gst.parse_launch('appsrc name=input format=time ! '
        'audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved ! '
        'webrtcdsp name=dsp ! appsink name=output emit-signals=true sync=false')
    AudioCapture.configure_processor(pipeline.get_by_name('dsp'), args.noise_reduction)
    pipeline.get_by_name('output').connect('new-sample', capture._new_sample)
    bus = pipeline.get_bus()
    bus.set_sync_handler(capture._voice_message)
    started = time.monotonic()
    try:
        pipeline.set_state(Gst.State.PLAYING)
        source = pipeline.get_by_name('input')
        for start in range(0, len(pcm), 160):
            data = pcm[start:start+160].tobytes()
            buffer = Gst.Buffer.new_allocate(None, len(data), None)
            buffer.fill(0, data)
            buffer.pts = start*Gst.SECOND//16000
            buffer.duration = len(data)*Gst.SECOND//32000
            if source.emit('push-buffer', buffer) != Gst.FlowReturn.OK:
                raise RuntimeError('Audio pipeline rejected a buffer')
        source.emit('end-of-stream')
        message = bus.timed_pop_filtered(30*Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            raise RuntimeError('Audio pipeline timed out')
        if message.type == Gst.MessageType.ERROR:
            raise RuntimeError(str(message.parse_error()))
    finally:
        pipeline.set_state(Gst.State.NULL)
        bus.set_sync_handler(None)
    result = {
        'noise_dbfs': args.noise_dbfs, 'gain': args.gain,
        'noise_reduction': args.noise_reduction,
        'audio_seconds': len(pcm)/16000, 'endpoint_seconds': times,
        'phrase_seconds': [round(len(c)/32000, 3) for c in chunks],
        'still_speaking_at_eof': capture._speaking,
        'pipeline_seconds': round(time.monotonic()-started, 3),
    }
    if args.transcribe:
        engine = WhisperEngine(model_path('base'), args.language)
        started = time.monotonic()
        result['text'] = [engine.transcribe(c) for c in chunks]
        result['inference_seconds'] = round(time.monotonic()-started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
