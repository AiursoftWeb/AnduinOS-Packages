"""Native integration is opt-in until the package build supplies the worker.

ANDUINOS_VOICE_WORKER=/absolute/worker ANDUINOS_VOICE_SAMPLE=/public/sample.wav
Both CPU-only and an available GPU are exercised; no microphone is opened.
"""
import os
import shutil
import tempfile
from pathlib import Path
import sys
import threading
import time
import unittest
import wave
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.errors import RecognitionCancelled, RecognitionError, ResidentUnavailable


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


@unittest.skipUnless(os.environ.get("ANDUINOS_VOICE_WORKER") and
                     os.environ.get("ANDUINOS_VOICE_SAMPLE"), "native worker/sample not supplied")
class NativeResidentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with wave.open(os.environ["ANDUINOS_VOICE_SAMPLE"], "rb") as sample:
            assert (sample.getframerate(), sample.getnchannels(), sample.getsampwidth()) == (16000, 1, 2)
            cls.pcm = sample.readframes(sample.getnframes())
        cls.model = Path(os.environ.get("ANDUINOS_VOICE_MODEL",
                                       "/usr/share/anduinos-whisper-framework/models/ggml-base.bin"))

    def engine(self, backend="cpu", **kwargs):
        return ResidentEngine(self.model, "en", 4, backend,
                              executable=os.environ["ANDUINOS_VOICE_WORKER"], **kwargs)

    def test_cpu_reuses_process_and_measures_real_stages(self):
        with self.engine() as engine:
            first = engine.transcribe(self.pcm)
            pid = engine.process.pid
            self.assertIn("initialization_ms", engine.last_metrics)
            second = engine.transcribe(self.pcm)
            self.assertEqual(engine.process.pid, pid)
            self.assertEqual(first, second)
            self.assertNotIn("initialization_ms", engine.last_metrics)
            self.assertEqual(engine.last_metrics["backend"], "cpu")
            self.assertGreater(engine.last_metrics["encode_ms"], 0)
            self.assertGreater(engine.last_metrics["batch_decode_ms"], 0)
        self.assertIsNone(engine.process)

    def test_warm_session_preparation_does_not_run_another_decode(self):
        from anduinos_whisper_framework.session_engine import SessionEngine
        session = SessionEngine(resident_factory=lambda *args, **kwargs: self.engine())
        self.addCleanup(session.close)
        self.assertTrue(session.prepare(self.model, 'en', self.pcm))
        worker = session.engine
        pid = worker.process.pid
        with patch.object(worker, 'transcribe', side_effect=AssertionError('Unexpected warm-up decode')):
            self.assertFalse(session.prepare(self.model, 'en', self.pcm))
        self.assertEqual(worker.process.pid, pid)
        self.assertTrue(worker.is_running())

    def test_gpu_candidate_reports_actual_backend(self):
        with self.engine("gpu") as engine:
            self.assertTrue(engine.transcribe(self.pcm))
            self.assertIn(engine.last_metrics["backend"], {"cpu", "gpu"})

    def test_missing_private_library_is_a_package_error(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / "worker"
            shutil.copy2(os.environ["ANDUINOS_VOICE_WORKER"], worker)
            with ResidentEngine(self.model, "en", 4, "gpu", executable=worker) as engine:
                with self.assertRaisesRegex(ResidentUnavailable, "missing or unsupported"):
                    engine.transcribe(self.pcm)
                self.assertIsNone(engine.process)

    def test_fresh_state_keeps_process_and_reports_lifecycle_without_fake_phases(self):
        with self.engine() as engine:
            first = engine.transcribe(self.pcm)
            pid = engine.process.pid
            second = engine.transcribe(self.pcm)
            self.assertEqual(engine.process.pid, pid)
            self.assertEqual(first, second)
            self.assertGreater(engine.last_metrics["state_initialization_ms"], 0)
            self.assertGreater(engine.last_metrics["state_release_ms"], 0)
            self.assertGreater(engine.last_metrics["encode_ms"], 0)
            self.assertGreater(engine.last_metrics["batch_decode_ms"], 0)
            phases = ("mel_ms", "encode_ms", "decode_ms", "batch_decode_ms", "sample_ms", "prompt_decode_ms")
            self.assertLessEqual(sum(engine.last_metrics[k] for k in phases),
                                 engine.last_metrics["inference_ms"] * 1.05)
            self.assertNotIn("initialization_ms", engine.last_metrics)

    def test_cancel_then_another_request_is_usable(self):
        with self.engine() as engine:
            engine.transcribe(self.pcm)
            event = threading.Event()
            timer = threading.Timer(0.1, event.set)
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaises(RecognitionCancelled):
                    engine.transcribe(self.pcm * 2, event)
            finally:
                timer.join()
            self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(engine.transcribe(self.pcm))

    def test_timeout_reaps_worker(self):
        with self.engine(timeout=0.001) as engine:
            with self.assertRaises(RecognitionError):
                engine.transcribe(self.pcm)
            self.assertIsNone(engine.process)

    def test_crash_is_reported_and_next_request_restarts(self):
        with self.engine() as engine:
            engine.transcribe(self.pcm)
            engine.process.kill()
            engine.process.wait(timeout=2)
            with self.assertRaises(RecognitionError):
                engine.transcribe(self.pcm)
            self.assertTrue(engine.transcribe(self.pcm))
