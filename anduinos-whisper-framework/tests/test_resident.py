"""Resident request validation without a native worker."""
from pathlib import Path
import sys
import threading
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.errors import RecognitionCancelled, RecognitionError


class ResidentValidationTests(unittest.TestCase):
    def test_invalid_configuration_is_rejected_before_spawning(self):
        for config in ({"backend": "shell"}, {"threads": 0}, {"beam": 6}):
            with self.assertRaises(ValueError):
                ResidentEngine(Path("/unused"), **config)

    def test_cancelled_request_does_not_start_a_process(self):
        event = threading.Event()
        event.set()
        engine = ResidentEngine(Path("/unused"))
        with self.assertRaises(RecognitionCancelled):
            engine.transcribe(b"\0" * 32000, event)
        self.assertIsNone(engine.process)

    def test_invalid_pcm_is_rejected(self):
        engine = ResidentEngine(Path("/unused"))
        for pcm in (b"\0" * 16001, b"\0" * (60 * 32000 + 2)):
            with self.assertRaises(RecognitionError):
                engine.transcribe(pcm)
