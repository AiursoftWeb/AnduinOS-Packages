"""Bounded pipe input and cancellation do not turn into recognised text."""
import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

PATH = Path(__file__).resolve().parents[1] / 'src/anduinos-parakeet-worker'
loader = importlib.machinery.SourceFileLoader('parakeet_worker', str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def test_invalid_options_do_not_load_weights_or_allocate_inference(self):
        for backend, threads in [('gpu', '4'), ('cpu', '0'), ('cpu', '257')]:
            result = subprocess.run([sys.executable, str(PATH), '/missing-model',
                                     'fr', threads, backend, '5'], capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')

    def test_cancellation_prevents_an_encoder_call(self):
        engine = worker.Parakeet.__new__(worker.Parakeet)
        engine.np = None
        # There are deliberately no inference sessions: cancellation must win first.
        with self.assertRaises(worker.Cancelled):
            engine.transcribe(bytes(32000), lambda: True)
