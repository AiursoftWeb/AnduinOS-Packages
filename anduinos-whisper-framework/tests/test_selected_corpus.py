import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("selected_corpus", ROOT / "tests/benchmarks/benchmark-selected.py")
selected = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selected)


class SelectedPolicyCorpusTests(unittest.TestCase):
    def run_corpus(self, regress=False, status="measured", actual="cpu"):
        class Selector:
            def __init__(self, *args): self.status = status
            def select(self, *args, **kwargs): return {"backend": "cpu", "threads": 8}
        class Reference:
            def __init__(self, *args, **kwargs): pass
            def transcribe(self, data): return "PRIVATE REFERENCE"
        class Engine(Reference):
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def transcribe(self, data):
                self.last_metrics = {"backend": actual}
                return "WRONG" if regress else "PRIVATE REFERENCE"
        output = io.StringIO()
        with patch.object(selected.sys, "argv", ["selected", "--worker", "unused"]), \
                patch.object(selected, "AutomaticSelector", Selector), \
                patch.object(selected, "ResidentEngine", Engine), \
                patch.object(selected.corpus, "WhisperEngine", Reference), \
                patch.object(selected.corpus, "units", side_effect=lambda text, lang: ["bad" if text == "WRONG" else "good"]), \
                patch("sys.stdout", output), patch("sys.stderr", io.StringIO()):
            code = selected.main()
        report = json.loads(output.getvalue())
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertEqual({r["language_mode"] for r in report["results"]}, {"auto", "en", "zh-Hans"})
        return code

    def test_measured_policy_is_checked_in_all_language_modes(self):
        self.assertEqual(self.run_corpus(), 0)

    def test_accuracy_regression_fails(self):
        self.assertEqual(self.run_corpus(regress=True), 1)

    def test_fallback_is_not_a_successful_measured_selection(self):
        self.assertEqual(self.run_corpus(status="fallback"), 1)

    def test_backend_must_match_measured_selection(self):
        self.assertEqual(self.run_corpus(actual="gpu"), 1)
