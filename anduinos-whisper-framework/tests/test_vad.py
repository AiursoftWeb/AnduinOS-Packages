import os
from pathlib import Path
import signal
import sys
import threading
import time
import unittest
from unittest.mock import Mock
import wave

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from anduinos_whisper_framework.vad import VadEngine
from anduinos_whisper_framework.errors import RecognitionCancelled, RecognitionError


class VadValidationTests(unittest.TestCase):
    def test_cancelled_start_does_not_spawn(self):
        engine = VadEngine('/unused')
        event = threading.Event(); event.set()
        with self.assertRaises(RecognitionCancelled):
            engine.start(event)
        self.assertIsNone(engine.process)

    def test_wrong_frame_or_unstarted_worker_is_rejected(self):
        engine = VadEngine('/unused')
        for size in (0, 1023, 1025, 32000):
            with self.assertRaises(ValueError):
                engine.classify(b'\0' * size)
        with self.assertRaises(RecognitionError):
            engine.classify(b'\0' * 1024)

    def test_invalid_response_is_rejected_and_process_closed(self):
        for probability in (True, '0.5', None, -0.1, 1.1, float('nan'), float('inf')):
            with self.subTest(probability=probability):
                engine = VadEngine('/unused')
                engine.process, engine.started = Mock(), True
                engine._exchange = Mock(return_value={'status': 'success', 'probability': probability})
                with self.assertRaises(RecognitionError):
                    engine.classify(b'\0' * 1024)
                self.assertIsNone(engine.process)
                self.assertFalse(engine.started)

    def test_only_successful_numeric_probability_is_returned(self):
        engine = VadEngine('/unused')
        engine.process, engine.started = Mock(), True
        self.addCleanup(engine.close)
        engine._exchange = Mock(return_value={'status': 'success', 'probability': 0.5,
                                             'text': 'must never be returned'})
        self.assertEqual(engine.classify(b'\0' * 1024), 0.5)
        engine._exchange.return_value = {'status': 'error', 'probability': 0.5}
        with self.assertRaises(RecognitionError):
            engine.classify(b'\0' * 1024)


@unittest.skipUnless(os.environ.get('ANDUINOS_VOICE_WORKER') and os.environ.get('ANDUINOS_VAD_MODEL'),
                     'native worker/VAD model not supplied')
class NativeVadTests(unittest.TestCase):
    def engine(self, **kwargs):
        engine = VadEngine(os.environ['ANDUINOS_VAD_MODEL'], os.environ['ANDUINOS_VOICE_WORKER'], **kwargs)
        self.addCleanup(engine.close)
        engine.start()
        return engine

    def test_silence_does_not_trigger_and_process_is_reused(self):
        engine = self.engine()
        pid = engine.process.pid
        for _ in range(100):
            self.assertLess(engine.classify(b'\0' * 1024), 0.5)
        self.assertEqual(engine.process.pid, pid)
        self.assertGreater(engine.ready_metrics['initialization_ms'], 0)
        engine.close()
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_quiet_public_english_triggers_reproducibly_after_restart(self):
        sample = Path(__file__).resolve().parents[1] / 'data/benchmark/en-short.wav'
        with wave.open(str(sample)) as audio:
            pcm = b'\0' * 32000 + audio.readframes(audio.getnframes()) + b'\0' * 64000
        engine = self.engine()
        def probabilities():
            return [engine.classify(pcm[i:i+1024].ljust(1024, b'\0'))
                    for i in range(0, len(pcm), 1024)]
        first = probabilities()
        self.assertTrue(any(p >= 0.5 for p in first))
        self.assertTrue(all(p < 0.5 for p in first[-20:]))
        engine.close(); engine.start()
        self.assertEqual(first, probabilities())

    def test_crash_does_not_silently_restart_in_middle_of_capture(self):
        engine = self.engine()
        engine.process.kill(); engine.process.wait(timeout=2)
        with self.assertRaises(RecognitionError):
            engine.classify(b'\0' * 1024)
        self.assertIsNone(engine.process)
        with self.assertRaises(RecognitionError):
            engine.classify(b'\0' * 1024)
        engine.start()
        self.assertLess(engine.classify(b'\0' * 1024), 0.5)

    def test_stalled_worker_is_killed_within_bound(self):
        engine = self.engine()
        engine.timeout = 0.05
        pid = engine.process.pid
        engine.process.send_signal(signal.SIGSTOP)
        started = time.monotonic()
        with self.assertRaises(RecognitionError):
            engine.classify(b'\0' * 1024)
        self.assertLess(time.monotonic() - started, 3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_cancellation_closes_recurrent_state(self):
        engine = self.engine()
        event = threading.Event(); event.set()
        with self.assertRaises(RecognitionCancelled):
            engine.classify(b'\0' * 1024, event)
        self.assertIsNone(engine.process)
