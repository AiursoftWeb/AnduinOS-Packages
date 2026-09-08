import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from anduinos_whisper_framework.tuning import AutomaticSelector, BackendTuner, SelectionCache, candidates, environment_fingerprint
from anduinos_whisper_framework.errors import RecognitionCancelled, RecognitionError


class TuningTests(unittest.TestCase):
    def test_candidates_are_small_and_respect_available_cpus(self):
        self.assertEqual(candidates(1), [("cpu", 1), ("gpu", 1)])
        self.assertEqual(candidates(3), [("cpu", 3), ("gpu", 3), ("cpu", 2)])
        self.assertEqual(len(candidates(128)), 4)

    def run_tuner(self, gpu_warm=0.2, gpu_actual="gpu", gpu_text="same", gpu_cold=8,
                  gpu_noisy=None):
        now = [0]
        seen = []
        class Engine:
            def __init__(self, model, language, threads, backend, **kwargs):
                self.backend, self.threads, self.calls = backend, threads, 0
                seen.append((model, kwargs["beam"]))
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def transcribe(self, pcm, cancel):
                now[0] += (gpu_cold if not self.calls else gpu_warm) if self.backend == "gpu" else 1
                self.calls += 1
                self.last_metrics = {"backend": gpu_actual if self.backend == "gpu" else "cpu",
                                     "threads": self.threads}
                if self.backend == "gpu" and self.calls >= 4 and gpu_noisy is not None:
                    return gpu_noisy[self.calls - 4]
                return gpu_text if self.backend == "gpu" else "same"
        tuner = BackendTuner(Engine, lambda: now[0])
        return tuner.run(Path("/same-model"), b"\x00\x01" * 8000, cpu_count=2), tuner, seen

    def test_cold_gpu_cost_does_not_hide_fast_warm_backend(self):
        selected, tuner, seen = self.run_tuner()
        self.assertEqual(selected["backend"], "gpu")
        self.assertEqual(sum(r["phase"] == "cold" for r in tuner.measurements), 2)
        self.assertEqual(set(seen), {(Path("/same-model"), 5)})

    def test_gpu_probe_that_used_cpu_is_not_claimed_as_gpu(self):
        selected, _, _ = self.run_tuner(gpu_actual="cpu")
        self.assertEqual(selected["backend"], "cpu")

    def test_faster_candidate_cannot_change_recognition_output(self):
        selected, _, _ = self.run_tuner(gpu_text="missing words")
        self.assertEqual(selected["backend"], "cpu")

    def test_small_gpu_improvement_prefers_cpu(self):
        selected, _, _ = self.run_tuner(gpu_warm=0.95)
        self.assertEqual(selected["backend"], "cpu")

    def test_fast_gpu_with_only_noisy_regression_is_rejected(self):
        selected, tuner, _ = self.run_tuner(gpu_noisy=("wrong word", "wrong word"))
        self.assertEqual(selected["backend"], "cpu")
        self.assertEqual(len(tuner.measurements), 9)

    def test_unstable_noisy_output_is_rejected(self):
        selected, _, _ = self.run_tuner(gpu_noisy=("same", "different"))
        self.assertEqual(selected["backend"], "cpu")

    def test_second_language_regression_rejects_fast_gpu(self):
        now, seen = [0], []
        class Engine:
            def __init__(self, model, language, threads, backend, **kwargs):
                self.backend, self.calls = backend, 0
                seen.append((language, threads, backend))
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def transcribe(self, pcm, cancel):
                self.calls += 1
                now[0] += 0.1 if self.backend == "gpu" else 1
                self.last_metrics = {"backend": self.backend}
                return "wrong Chinese" if self.backend == "gpu" and self.calls >= 8 else "same"
        tuner = BackendTuner(Engine, lambda: now[0])
        selected = tuner.run("model", b"\x00\x01" * 8000, language="auto",
                             additional_pcm=(b"\x00\x02" * 8000,), threads=3)
        self.assertEqual(selected, {"backend": "cpu", "threads": 3})
        self.assertEqual(seen, [("auto", 3, "cpu"), ("auto", 3, "gpu")])
        self.assertEqual(len(tuner.measurements), 17)
        self.assertEqual(sum(r["phase"] == "cold" for r in tuner.measurements), 2)

    def test_slow_candidate_is_pruned_but_winner_finishes_all_quality_checks(self):
        now, requests = [0], {}
        class Engine:
            def __init__(self, model, language, threads, backend, **kwargs):
                self.key = (backend, threads)
                self.last_metrics = {"backend": backend}
                requests[self.key] = 0
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def transcribe(self, pcm, cancel):
                requests[self.key] += 1
                now[0] += {("cpu", 4): 1, ("gpu", 4): 0.1,
                           ("cpu", 8): 0.6, ("cpu", 2): 3}[self.key]
                # A very fast but inaccurate GPU must not prune CPU alternatives.
                return "wrong" if self.key[0] == "gpu" else "same"
        tuner = BackendTuner(Engine, lambda: now[0])
        selected = tuner.run("model", b"\x00\x01" * 8000, cpu_count=8,
                             additional_pcm=(b"\x00\x02" * 8000,))
        self.assertEqual(selected, {"backend": "cpu", "threads": 8})
        self.assertEqual(requests, {("cpu", 4): 9, ("gpu", 4): 2,
                                    ("cpu", 8): 9, ("cpu", 2): 3})

    def test_deadline_stops_before_starting_another_request(self):
        now, calls = [0], []
        class Engine:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def transcribe(self, pcm, cancel):
                calls.append(now[0])
                now[0] += 30
                self.last_metrics = {"backend": "cpu"}
                return "same"
        tuner = BackendTuner(Engine, lambda: now[0])
        with self.assertRaises(RecognitionError):
            tuner.run("model", b"\x00\x01" * 8000, cpu_count=2)
        self.assertEqual(calls, [0, 30])

    def test_cache_is_private_bounded_validated_and_invalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "performance.json"
            cache = SelectionCache(path)
            cache.save("fingerprint", {"backend": "cpu", "threads": 1},
                       [{"kind": "benchmark", "text": "SECRET", "stderr": "SECRET"}])
            self.assertEqual(cache.load("fingerprint"), {"backend": "cpu", "threads": 1})
            self.assertIsNone(cache.load("changed-driver"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("SECRET", path.read_text())
            path.write_text('{"selected": {"backend":"shell"}}')
            self.assertIsNone(cache.load("fingerprint"))
            path.write_text('x' * 65537)
            self.assertIsNone(cache.load("fingerprint"))

    def test_worker_or_model_change_invalidates_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model"
            worker = Path(directory) / "worker"
            model.touch(); worker.touch()
            first = environment_fingerprint(model, "sample", worker)
            model.write_bytes(b"changed")
            self.assertNotEqual(first, environment_fingerprint(model, "sample", worker))

    def test_private_library_change_invalidates_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / "worker"
            model = Path(directory) / "model"
            worker.touch(); model.touch()
            library = worker.parent / "anduinos-whisper" / "libanduinos-whisper.so.1"
            library.parent.mkdir()
            library.write_bytes(b"first")
            first = environment_fingerprint(model, "sample", worker)
            library.write_bytes(b"second version")
            self.assertNotEqual(first, environment_fingerprint(model, "sample", worker))

    def test_ggml_core_update_invalidates_selection_without_plugin_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            model, worker = Path(directory) / 'model', Path(directory) / 'worker'
            model.touch(); worker.touch()
            core = Path(directory) / 'libggml-base.so.0'
            core.write_bytes(b'old core')
            original_glob = Path.glob
            def glob(path, pattern):
                if path == Path('/usr/lib') and pattern == '*/libggml*.so*':
                    return iter([core])
                return original_glob(path, pattern)
            with patch.object(Path, 'glob', glob):
                first = environment_fingerprint(model, 'sample', worker)
                core.write_bytes(b'updated core, same worker and CPU plugin')
                self.assertNotEqual(first, environment_fingerprint(model, 'sample', worker))

    def test_bundled_audio_is_pinned_and_matches_format(self):
        directory = ROOT / "data" / "benchmark"
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(manifest["license"], "CC-BY-4.0")
        for sample in manifest["samples"]:
            path = directory / sample["file"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), sample["sha256"])
            with wave.open(str(path), "rb") as audio:
                self.assertEqual((audio.getnchannels(), audio.getframerate(), audio.getsampwidth()), (1, 16000, 2))
                self.assertLess(audio.getnframes(), 12 * 16000)


class AutomaticSelectorTests(unittest.TestCase):
    def setUp(self):
        self.cache, self.tuner = Mock(), Mock()
        self.cache.load.return_value = None
        self.tuner.run.return_value = {"backend": "gpu", "threads": 1}
        self.tuner.measurements = []
        self.selector = AutomaticSelector(ROOT / "data" / "benchmark", self.cache, self.tuner)

    def test_manual_selection_does_not_run_a_benchmark(self):
        self.assertEqual(self.selector.select("unused", backend="cpu", threads=1),
                         {"backend": "cpu", "threads": 1})
        self.tuner.run.assert_not_called()
        self.assertEqual(self.selector.status, "manual")

    def test_measurements_are_private_and_not_replayed_on_manual_or_cached_use(self):
        self.tuner.measurements = [{"kind": "benchmark", "inference_ms": 23,
                                    "text": "PRIVATE", "stderr": "PRIVATE"}]
        self.selector.select("unused")
        self.assertEqual(self.selector.measurements,
                         [{"kind": "benchmark", "inference_ms": 23}])
        self.selector.select("unused", backend="cpu")
        self.assertEqual(self.selector.measurements, [])
        self.selector.select("unused", force=True)
        self.cache.load.return_value = {"backend": "cpu", "threads": 1}
        self.selector.select("unused")
        self.assertEqual(self.selector.measurements, [])

    def test_partial_measurements_survive_error_and_cancel_but_are_bounded(self):
        self.tuner.measurements = [{"kind": "benchmark", "inference_ms": i,
                                    "text": "PRIVATE"} for i in range(120)]
        for error in (RecognitionError("error"), RecognitionCancelled()):
            self.tuner.run.side_effect = error
            try:
                self.selector.select("unused", force=True)
            except RecognitionCancelled:
                pass
            self.assertEqual(len(self.selector.measurements), 100)
            self.assertEqual(self.selector.measurements[0]["inference_ms"], 20)
            self.assertNotIn("PRIVATE", json.dumps(self.selector.measurements))

    def test_measurement_notice_only_runs_for_actual_calibration(self):
        notice = Mock()
        self.selector.select("unused", backend="cpu", on_measure=notice)
        notice.assert_not_called()
        self.cache.load.return_value = {"backend": "cpu", "threads": 1}
        self.selector.select("unused", on_measure=notice)
        notice.assert_not_called()
        self.selector.select("unused", force=True, on_measure=notice)
        notice.assert_called_once_with()

    def test_cached_selection_and_explicit_retest(self):
        self.cache.load.return_value = {"backend": "cpu", "threads": 1}
        self.assertEqual(self.selector.select("unused")["backend"], "cpu")
        self.tuner.run.assert_not_called()
        self.assertEqual(self.selector.select("unused", force=True)["backend"], "gpu")
        self.tuner.run.assert_called_once()
        self.assertEqual(self.selector.status, "measured")

    def test_chinese_setting_uses_chinese_fixture(self):
        self.selector.select("unused", "zh-Hans")
        self.assertEqual(self.tuner.run.call_args.args[2], "zh-Hans")

    def test_auto_language_checks_both_fixtures_in_auto_mode(self):
        self.selector.select("unused")
        call = self.tuner.run.call_args
        self.assertEqual(call.args[2], "auto")
        self.assertEqual(call.args[1], self.selector.calibration("en")[0])
        self.assertEqual(call.kwargs["additional_pcm"], (self.selector.calibration("zh-Hans")[0],))

    def test_manual_threads_are_measured_and_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SelectionCache(Path(directory) / "performance.json")
            selector = AutomaticSelector(ROOT / "data" / "benchmark", cache, self.tuner)
            with patch("anduinos_whisper_framework.tuning.available_threads", return_value=8):
                self.tuner.run.return_value = {"backend": "cpu", "threads": 2}
                selector.select("unused", threads=2)
                selector.select("unused", threads=2)
                self.assertEqual(self.tuner.run.call_count, 1)
                self.assertEqual(self.tuner.run.call_args.kwargs["threads"], 2)
                self.tuner.run.return_value = {"backend": "cpu", "threads": 3}
                self.assertEqual(selector.select("unused", threads=3)["threads"], 3)
                self.assertEqual(self.tuner.run.call_count, 2)
                self.assertEqual(self.tuner.run.call_args.kwargs["threads"], 3)

    def test_language_mode_invalidates_cache_even_for_same_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SelectionCache(Path(directory) / "performance.json")
            selector = AutomaticSelector(ROOT / "data" / "benchmark", cache, self.tuner)
            selector.select("unused", language="en")
            selector.select("unused", language="en")
            self.assertEqual(self.tuner.run.call_count, 1)
            selector.select("unused", language="fr")
            self.assertEqual(self.tuner.run.call_count, 2)
            self.assertEqual(self.tuner.run.call_args.args[2], "fr")

    def test_cancel_does_not_become_a_cpu_fallback(self):
        self.tuner.run.side_effect = RecognitionCancelled()
        with self.assertRaises(RecognitionCancelled):
            self.selector.select("unused")
        self.cache.save.assert_not_called()

    def test_failed_benchmark_is_not_repeated_until_explicit_retry(self):
        self.tuner.run.side_effect = RecognitionError("backend error")
        self.assertEqual(self.selector.select("unused")["backend"], "cpu")
        self.selector.select("unused")
        self.assertEqual(self.tuner.run.call_count, 1)
        self.selector.select("unused", force=True)
        self.assertEqual(self.tuner.run.call_count, 2)

    def test_readonly_cache_does_not_discard_measured_result(self):
        self.cache.save.side_effect = PermissionError()
        self.assertEqual(self.selector.select("unused")["backend"], "gpu")

    def test_generation_invalidates_persistent_cache_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = SelectionCache(Path(directory) / "performance.json")
            first = AutomaticSelector(ROOT / "data" / "benchmark", cache, self.tuner)
            first.select("unused", generation=4)
            restarted = AutomaticSelector(ROOT / "data" / "benchmark", cache, self.tuner)
            restarted.select("unused", generation=4)
            self.assertEqual(self.tuner.run.call_count, 1)
            restarted.select("unused", generation=5)
            self.assertEqual(self.tuner.run.call_count, 2)

    def test_generation_retries_failed_probe_and_preserves_manual_threads(self):
        self.tuner.run.side_effect = RecognitionError("backend error")
        with patch("anduinos_whisper_framework.tuning.available_threads", return_value=8):
            for generation in (0, 0, 1):
                self.assertEqual(self.selector.select("unused", threads=2, generation=generation),
                                 {"backend": "cpu", "threads": 2})
        self.assertEqual(self.tuner.run.call_count, 2)

    def test_already_cancelled_selection_never_uses_cache_or_manual_path(self):
        cancel = threading.Event()
        cancel.set()
        for backend in ("auto", "cpu", "gpu"):
            with self.assertRaises(RecognitionCancelled):
                self.selector.select("unused", backend=backend, cancel=cancel)
        self.cache.load.assert_not_called()
        self.tuner.run.assert_not_called()

    def test_invalid_generation_is_rejected(self):
        for generation in (-1, True, "1", 0x100000000):
            with self.assertRaises(ValueError):
                self.selector.select("unused", generation=generation)
