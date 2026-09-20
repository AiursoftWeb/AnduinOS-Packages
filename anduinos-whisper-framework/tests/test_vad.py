from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock

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
