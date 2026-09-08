"""Native behavior with explicit source-built workers and public model fixtures."""
from array import array
import os
from pathlib import Path
import random
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from anduinos_whisper_framework.audio import AudioCapture, Gst
from anduinos_whisper_framework.resident import ResidentEngine
from anduinos_whisper_framework.vad import VadEngine
from anduinos_whisper_framework.errors import RecognitionCancelled, RecognitionError, ResidentUnavailable

for variable in ("ANDUINOS_VOICE_WORKER", "ANDUINOS_VOICE_SAMPLE", "ANDUINOS_VOICE_MODEL", "ANDUINOS_VAD_MODEL"):
    if not os.environ.get(variable) or not Path(os.environ[variable]).is_file():
        raise RuntimeError(f"{variable} must name a source-built artifact or public fixture")


class NativeResidentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with wave.open(os.environ["ANDUINOS_VOICE_SAMPLE"], "rb") as sample:
            assert (sample.getframerate(), sample.getnchannels(), sample.getsampwidth()) == (16000, 1, 2)
            cls.pcm = sample.readframes(sample.getnframes())
        cls.model = Path(os.environ["ANDUINOS_VOICE_MODEL"])

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
        sample = Path(__file__).resolve().parents[2] / 'data/benchmark/en-short.wav'
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



class NativeDspTests(unittest.TestCase):
    def setUp(self):
        self.chunks, self.partials, self.errors = [], [], []
        self.capture = AudioCapture('', self.chunks.append, self.partials.append,
                                    lambda _: None, self.errors.append, lambda: None)

    def test_real_dsp_rejects_stationary_noise_without_opening_microphone(self):
        self.capture._vad = VadEngine(os.environ['ANDUINOS_VAD_MODEL'], os.environ['ANDUINOS_VOICE_WORKER'])
        self.capture._vad.start()
        self.addCleanup(self.capture.stop, False)
        if Gst.ElementFactory.find('webrtcdsp') is None:
            self.fail('Install gstreamer1.0-plugins-bad for DSP integration')
        pipeline = Gst.parse_launch('appsrc name=input format=time ! '
            'audio/x-raw,format=S16LE,rate=16000,channels=1,layout=interleaved ! '
            'webrtcdsp name=dsp ! appsink name=output emit-signals=true sync=false')
        AudioCapture.configure_processor(pipeline.get_by_name('dsp'), False)
        pipeline.get_by_name('output').connect('new-sample', self.capture._new_sample)
        bus = pipeline.get_bus()
        rng = random.Random(42)
        try:
            pipeline.set_state(Gst.State.PLAYING)
            source = pipeline.get_by_name('input')
            for index in range(1500):  # 15 s of -35 dBFS synthetic white noise
                samples = array('h', [int(rng.gauss(0, 580)) for _ in range(160)])
                buffer = Gst.Buffer.new_allocate(None, 320, None)
                buffer.fill(0, samples.tobytes())
                buffer.pts = index * Gst.SECOND // 100
                buffer.duration = Gst.SECOND // 100
                self.assertEqual(source.emit('push-buffer', buffer), Gst.FlowReturn.OK)
            source.emit('end-of-stream')
            message = bus.timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            self.assertIsNotNone(message)
            self.assertEqual(message.type, Gst.MessageType.EOS)
            self.assertEqual(self.chunks, [])
            self.assertFalse(self.capture._speaking)
        finally:
            pipeline.set_state(Gst.State.NULL)
            bus.set_sync_handler(None)
