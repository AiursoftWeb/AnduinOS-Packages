import json
from array import array
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from anduinos_whisper_framework.diagnostics import PerformanceHistory, parse_cli_backend, parse_cli_timings, sanitize
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_engine import WhisperEngine
from anduinos_whisper_framework.audio import AudioCapture
from anduinos_whisper_framework.daemon import VoiceTypingService
from gi.repository import GLib


class DiagnosticsTests(unittest.TestCase):
    def test_cli_backend_requires_activation_not_device_discovery(self):
        self.assertEqual(parse_cli_backend("ggml_cuda_init: found 1 CUDA devices\nDevice 0: SECRET"), "unknown")
        self.assertEqual(parse_cli_backend("whisper_backend_init_gpu: using CUDA0 backend\nSECRET"), "gpu")
        self.assertEqual(parse_cli_backend(""), "unknown")

    def test_delivery_is_linked_once_and_old_tickets_are_evicted(self):
        history = PerformanceHistory(limit=2)
        ticket = history.append({"kind": "final"})
        history.ready_for_delivery(ticket, 10.0)
        self.assertTrue(history.acknowledge_delivery(ticket, 10.385))
        self.assertFalse(history.acknowledge_delivery(ticket, 11))
        report = json.loads(history.export_json())["measurements"]
        self.assertEqual(report[0]["delivery_ms"], 385)
        self.assertNotIn("ticket", report[0])
        stale = history.append({"kind": "final"})
        history.ready_for_delivery(stale, 12)
        history.append({"kind": "partial"})
        history.append({"kind": "partial"})
        self.assertFalse(history.acknowledge_delivery(stale, 13))
        self.assertEqual(history._delivery_started, {})

    def test_only_shell_can_acknowledge_delivery(self):
        service = VoiceTypingService.__new__(VoiceTypingService)
        service.shell_owner = ":1.42"
        service.performance = Mock()
        denied = Mock()
        service._method_called(None, ":1.99", "", "", "ReportDelivery", GLib.Variant("(u)", (1,)), denied)
        denied.return_dbus_error.assert_called_once()
        service.performance.acknowledge_delivery.assert_not_called()
        allowed = Mock()
        service._method_called(None, ":1.42", "", "", "ReportDelivery", GLib.Variant("(u)", (1,)), allowed)
        service.performance.acknowledge_delivery.assert_called_once()
        self.assertEqual(service.performance.acknowledge_delivery.call_args.args[0], 1)
        allowed.return_value.assert_called_once_with(None)

    def capture(self, reports, **kwargs):
        return AudioCapture("", lambda _: None, lambda _: None, lambda _: None,
                            lambda _: None, lambda: None,
                            on_chunk_metrics=lambda pcm, metrics: reports.append((pcm, metrics)), **kwargs)

    def test_endpoint_reports_audio_tail_not_inference_time(self):
        reports = []
        capture = self.capture(reports)
        pcm = array("h", [1000] * 1600).tobytes()
        for _ in range(10):
            capture._consume(pcm, voiced=True)
        for _ in range(10):
            capture._consume(pcm, voiced=False)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][1]["endpoint_reason"], "silence")
        self.assertGreaterEqual(reports[0][1]["endpoint_ms"], 800)
        self.assertLess(reports[0][1]["endpoint_ms"], 901)

    def test_finish_and_maximum_length_have_distinct_endpoint_reasons(self):
        reports = []
        capture = self.capture(reports, max_phrase_seconds=1)
        pcm = array("h", [1000] * 1600).tobytes()
        for _ in range(10):
            capture._consume(pcm, voiced=True)
        self.assertEqual(reports[0][1]["endpoint_reason"], "max-duration")
        self.assertEqual(reports[0][1]["endpoint_ms"], 0)
        for _ in range(3):
            capture._consume(pcm, voiced=True)
        capture._consume(pcm, voiced=False)
        capture.stop(flush=True)
        self.assertEqual(reports[-1][1]["endpoint_reason"], "finish")
        self.assertAlmostEqual(reports[-1][1]["endpoint_ms"], 100)

    def test_backend_output_is_not_an_export_format(self):
        private = "Private spoken sentence /home/person/recording.wav"
        log = (private + "\nwhisper_print_timings: load time = 10.25 ms\n"
               "whisper_print_timings: encode time = 81.5 ms / 1 runs\n"
               "whisper_print_timings: batchd time = 12.00 ms / 42 runs\n"
               "ggml_vulkan: Found 1 Vulkan devices\n")
        self.assertEqual(parse_cli_timings(log), {
            "load_ms": 10.25, "encode_ms": 81.5, "batch_decode_ms": 12.0})

    def test_export_is_bounded_and_allowlisted(self):
        history = PerformanceHistory(limit=2)
        for index in range(3):
            history.append({"queue_ms": index, "text": "secret", "pcm": b"secret",
                            "error": "secret", "backend": "secret", "model": "base"})
        exported = history.export_json()
        self.assertNotIn("secret", exported)
        self.assertEqual([r["queue_ms"] for r in json.loads(exported)["measurements"]], [1, 2])

    def test_missing_and_invalid_measurements_are_not_zero(self):
        self.assertEqual(sanitize({"encode_ms": float("nan"), "load_ms": -1,
                                   "queue_ms": True, "decode_ms": float("inf"),
                                   "threads": 0}), {})
        self.assertEqual(parse_cli_timings("unknown upstream output"), {})

    @patch("benchmark_engine.subprocess.run")
    def test_engine_collects_timings_without_changing_transcript(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "hello", (
            "whisper_print_timings: encode time = 80.0 ms\nprivate log"))
        engine = WhisperEngine(Path("/model.bin"), "en", 4)
        with patch("pathlib.Path.is_file", return_value=True):
            self.assertEqual(engine.transcribe(b"\0" * 32000), "hello")
        self.assertEqual(engine.last_metrics["encode_ms"], 80.0)
        self.assertEqual(engine.last_metrics["audio_ms"], 1000)
        self.assertEqual(engine.last_metrics["backend"], "unknown")
        self.assertNotIn("private", str(engine.last_metrics))
        self.assertNotIn("--no-prints", run.call_args.args[0])
        with patch("pathlib.Path.is_file", return_value=True):
            engine.transcribe(b"")
        self.assertEqual(engine.last_metrics, {})
