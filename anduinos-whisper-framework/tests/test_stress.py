import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("resident_stress", ROOT / "scripts/stress-resident.py")
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)


class StressAcceptanceTests(unittest.TestCase):
    def run_stress(self, resource_growth=None, unstable=False, actual_backend="cpu"):
        class Engine:
            def __init__(self, *args, **kwargs):
                self.process = Mock(pid=123)
                self.process.poll.return_value = 0
                self.calls = 0
                self.last_metrics = {"backend": actual_backend}
            def transcribe(self, data, cancel=None):
                if cancel is not None:
                    raise stress.RecognitionCancelled()
                self.calls += 1
                return "changed" if unstable and self.calls > 4 else "stable"
            def close(self): pass
        resource_calls = [0]
        def resources(_pid):
            resource_calls[0] += 1
            state = {"rss_mib": 128, "fds": 3, "threads": 1}
            if resource_growth and resource_calls[0] > 9:
                state.update(resource_growth)
            return state
        output = io.StringIO()
        with patch.object(stress.sys, "argv", ["stress", "--worker", "unused", "--requests", "32"]), \
                patch.object(stress, "ResidentEngine", Engine), \
                patch.object(stress, "resources", resources), \
                patch("sys.stdout", output), patch("sys.stderr", io.StringIO()):
            stress.main()
        return json.loads(output.getvalue())

    def test_stable_run_checks_cancellation_and_reports_only_metadata(self):
        report = self.run_stress()
        self.assertTrue(report["passed"])
        self.assertEqual(report["cancellations"], 3)
        self.assertNotIn("stable", json.dumps(report))

    def test_rss_growth_fails(self):
        with self.assertRaisesRegex(AssertionError, "RSS growth"):
            self.run_stress(resource_growth={"rss_mib": 256})

    def test_fd_and_thread_growth_fail(self):
        for growth in ({"fds": 4}, {"threads": 2}):
            with self.assertRaises(AssertionError):
                self.run_stress(resource_growth=growth)

    def test_output_instability_fails(self):
        with self.assertRaisesRegex(AssertionError, "output changed"):
            self.run_stress(unstable=True)

    def test_wrong_backend_cannot_pass(self):
        with self.assertRaisesRegex(AssertionError, "backend was not exercised"):
            self.run_stress(actual_backend="gpu")
