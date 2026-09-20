from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_whisper_framework.session_engine import SessionEngine
from anduinos_whisper_framework.errors import RecognitionCancelled, RecognitionError, ResidentUnavailable
from anduinos_whisper_framework.work_queue import RecognitionQueue


class SessionEngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.model = Path(self.directory.name) / "model.bin"
        self.model.touch()
        self.resident = Mock()
        self.resident.return_value.transcribe.return_value = "recognized"
        self.resident.return_value.last_metrics = {}
        self.clock = Mock(return_value=0)
        self.session = SessionEngine(self.resident, idle_seconds=10, clock=self.clock)
        self.addCleanup(self.session.close)

    def run_task(self, **kwargs):
        return self.session.transcribe(self.model, "en", b"audio", **kwargs)

    def test_same_configuration_reuses_worker_and_idle_releases_it(self):
        self.run_task()
        self.run_task()
        self.assertEqual(self.resident.call_count, 1)
        self.clock.return_value = 9
        self.session.release_if_idle()
        self.assertIsNotNone(self.session.engine)
        self.clock.return_value = 10
        self.session.release_if_idle()
        self.assertIsNone(self.session.engine)
        self.run_task()
        self.assertEqual(self.resident.call_count, 2)

    def test_preparation_reuses_warm_worker_without_another_fixture_decode(self):
        self.resident.return_value.is_running.return_value = True
        self.assertTrue(self.session.prepare(self.model, 'en', b'fixture'))
        self.clock.return_value = 5
        self.assertFalse(self.session.prepare(self.model, 'en', b'fixture'))
        self.assertEqual(self.session.last_used, 5)
        self.assertEqual(self.session.last_metrics, {})
        self.resident.return_value.transcribe.assert_called_once()
        self.model.write_bytes(b'new model')
        self.assertTrue(self.session.prepare(self.model, 'en', b'fixture'))
        self.assertEqual(self.resident.call_count, 2)

    def test_preparation_restarts_worker_that_died_while_idle(self):
        self.session.prepare(self.model, 'en', b'fixture')
        self.resident.return_value.is_running.return_value = False
        self.assertTrue(self.session.prepare(self.model, 'en', b'fixture'))
        self.assertEqual(self.resident.call_count, 2)
        self.resident.return_value.close.assert_called_once()

    def test_cancelled_preparation_does_not_start_or_warm(self):
        event = threading.Event(); event.set()
        with self.assertRaises(RecognitionCancelled):
            self.session.prepare(self.model, 'en', b'fixture', event)
        self.resident.assert_not_called()

    def test_configuration_and_model_replacement_reload(self):
        self.run_task(threads=2)
        self.run_task(threads=4)
        self.assertEqual(self.resident.call_count, 2)
        self.model.write_bytes(b"replacement")
        self.run_task(threads=4)
        self.assertEqual(self.resident.call_count, 3)
        self.assertEqual(self.resident.return_value.close.call_count, 2)

    def test_gpu_failure_retries_once_on_cpu_without_changing_model(self):
        gpu, cpu = Mock(), Mock()
        gpu.transcribe.side_effect = RecognitionError("device lost")
        cpu.transcribe.return_value = "recognized"
        cpu.last_metrics = {"backend": "cpu"}
        self.resident.side_effect = [gpu, cpu]
        self.assertEqual(self.run_task(backend="gpu"), "recognized")
        self.run_task(backend="gpu")
        self.assertEqual(self.resident.call_count, 2)
        self.assertEqual(self.resident.call_args.kwargs["backend"], "cpu")
        self.assertEqual(self.resident.call_args.args[0], self.model)
        self.assertEqual(self.session.last_metrics["fallback"], "gpu_failed")

    def test_invalidation_retries_revalidated_gpu_but_idle_release_does_not(self):
        gpu, cpu, idle_cpu, recovered = Mock(), Mock(), Mock(), Mock()
        gpu.transcribe.side_effect = RecognitionError("device lost")
        for worker in (cpu, idle_cpu, recovered):
            worker.transcribe.return_value = "recognized"
            worker.last_metrics = {}
        self.resident.side_effect = [gpu, cpu, idle_cpu, recovered]
        self.run_task(backend="gpu")
        self.clock.return_value = 10
        self.session.release_if_idle()
        self.run_task(backend="gpu")
        self.assertEqual(self.resident.call_args.kwargs["backend"], "cpu")
        self.session.invalidate()
        idle_cpu.close.assert_called_once()
        self.assertFalse(self.session.gpu_failed)
        self.assertIsNone(self.session.key)
        self.assertEqual(self.session.last_metrics, {})
        self.run_task(backend="gpu")
        self.run_task(backend="gpu")
        self.assertEqual(self.resident.call_count, 4)
        self.assertEqual(self.resident.call_args.kwargs["backend"], "gpu")
        self.assertNotIn("fallback", self.session.last_metrics)

    def test_invalidation_reloads_healthy_worker_for_updated_libraries(self):
        self.run_task()
        self.session.invalidate()
        self.resident.return_value.close.assert_called_once()
        self.run_task()
        self.assertEqual(self.resident.call_count, 2)

    def test_unsupported_abi_is_reported_without_any_backend_retry(self):
        self.resident.return_value.transcribe.side_effect = ResidentUnavailable("ABI")
        for backend in ("cpu", "gpu"):
            before = self.resident.call_count
            with self.assertRaises(ResidentUnavailable):
                self.run_task(backend=backend)
            self.assertEqual(self.resident.call_count, before + 1)
            self.assertIsNone(self.session.engine)
        self.assertEqual(self.session.last_metrics, {})

    def test_failed_cpu_retry_is_released_without_a_third_attempt(self):
        gpu, cpu = Mock(), Mock()
        gpu.transcribe.side_effect = RecognitionError("device lost")
        cpu.transcribe.side_effect = RecognitionError("CPU error")
        self.resident.side_effect = [gpu, cpu]
        with self.assertRaises(RecognitionError):
            self.run_task(backend="gpu")
        self.assertEqual(self.resident.call_count, 2)
        gpu.close.assert_called_once()
        cpu.close.assert_called_once()
        self.assertIsNone(self.session.engine)

    def test_cancellation_never_triggers_fallback(self):
        self.resident.return_value.transcribe.side_effect = RecognitionCancelled()
        with self.assertRaises(RecognitionCancelled):
            self.run_task(backend="gpu", cancel=threading.Event())
        self.assertEqual(self.resident.call_count, 1)

    def test_cpu_error_does_not_loop(self):
        self.resident.return_value.transcribe.side_effect = RecognitionError("bad inference")
        with self.assertRaises(RecognitionError):
            self.run_task()
        self.assertEqual(self.resident.call_count, 1)
        self.assertIsNone(self.session.engine)

    def test_idle_queue_timeout_is_not_a_quit(self):
        queue = RecognitionQueue()
        self.assertIsNone(queue.get(timeout=0))
        queue.put((0, "final"))
        self.assertEqual(queue.get(timeout=0), (0, "final"))
