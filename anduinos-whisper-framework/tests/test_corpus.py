from array import array
import importlib.util
import math
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("corpus_benchmark", ROOT / "scripts" / "benchmark-corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


class CorpusScoringTests(unittest.TestCase):
    def test_gpu_cli_comparison_cannot_replace_cpu_accuracy_baseline(self):
        class Engine:
            def __init__(self, *args, backend, **kwargs):
                self.last_metrics = {"backend": backend}
            def transcribe(self, data):
                return "bad" if self.last_metrics["backend"] == "gpu" else "good"
            def close(self):
                pass
        output = io.StringIO()
        with patch.object(corpus.sys, "argv", ["benchmark", "--gpu", "--compare-cli-gpu"]), \
                patch.object(corpus, "WhisperEngine", Engine), \
                patch.object(corpus, "ResidentEngine", Engine), \
                patch.object(corpus, "units", side_effect=lambda text, language: ["bad" if text == "bad" else "good"]), \
                patch("sys.stdout", output):
            self.assertEqual(corpus.main(), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report["accuracy_nonregression"])
        self.assertEqual(len(report["results"]), 32)
        for result in report["results"]:
            self.assertEqual(result["errors"], int(result["mode"].endswith("-gpu")))

    def test_english_words_and_chinese_characters_are_scored_separately(self):
        self.assertEqual(corpus.units("Hello, WORLD!", "en"), ["hello", "world"])
        self.assertEqual(corpus.units("你好，世界！", "zh-Hans"), list("你好世界"))
        self.assertEqual(corpus.edit_distance(["hello", "world"], ["hello"]), 1)
        self.assertEqual(corpus.edit_distance(list("你好世界"), list("你好世间")), 1)
        self.assertEqual(corpus.edit_distance([], ["extra"]), 1)

    def test_noise_is_repeatable_and_has_the_requested_snr(self):
        original = array("h", (round(3000 * math.sin(i / 10)) for i in range(16000)))
        pcm = original.tobytes()
        first = corpus.noisy(pcm)
        self.assertEqual(first, corpus.noisy(pcm))
        self.assertEqual(original.tobytes(), pcm)
        modified = array("h"); modified.frombytes(first)
        snr = 10 * math.log10(sum(x*x for x in original) /
                             sum((x-y)**2 for x, y in zip(original, modified)))
        self.assertAlmostEqual(snr, 20, delta=0.2)
