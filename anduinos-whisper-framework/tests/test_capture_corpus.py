import importlib.util
import math
from array import array
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture_benchmark', ROOT / 'scripts/benchmark-capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
noise_spec = importlib.util.spec_from_file_location('noise_benchmark', ROOT / 'scripts/benchmark-noise.py')
noise = importlib.util.module_from_spec(noise_spec)
noise_spec.loader.exec_module(noise)


class CaptureCorpusTests(unittest.TestCase):
    def test_non_speech_shapes_are_reproducible_and_levelled(self):
        for shape in ('hum', 'fan-like', 'tapping'):
            pcm = noise.noise(shape, -35, seconds=1)
            self.assertEqual(len(pcm), 32000)
            self.assertEqual(pcm, noise.noise(shape, -35, seconds=1))
            self.assertAlmostEqual(capture.rms_dbfs(pcm), -35, delta=0.05)

    def test_noise_is_continuous_reproducible_and_uses_speech_snr(self):
        original = array('h', (round(2000 * math.sin(i / 10)) for i in range(16000)))
        pcm = original.tobytes()
        data = capture.stimulus(pcm, snr=20)
        self.assertEqual(data, capture.stimulus(pcm, snr=20))
        self.assertEqual(len(data), len(pcm) + 4 * 32000)
        values = array('h'); values.frombytes(data)
        self.assertTrue(any(values[:16000]))
        self.assertTrue(any(values[-48000:]))
        snr = 10 * math.log10(sum(x*x for x in original) /
            sum((x-y)**2 for x, y in zip(original, values[16000:32000])))
        self.assertAlmostEqual(snr, 20, delta=0.2)
        quiet = array('h'); quiet.frombytes(capture.stimulus(pcm, snr=20, gain=0.1))
        self.assertLess(max(abs(x) for x in quiet), max(abs(x) for x in values) / 9)

    def test_real_dsp_does_not_flush_silence_as_speech_at_eos(self):
        if capture.Gst.ElementFactory.find('webrtcdsp') is None:
            self.skipTest('WebRTC DSP is unavailable')
        for reduction in (False, True):
            chunks, events, listening = capture.capture_fixture(b'\0' * 32000, reduction, reference_webrtc=True)
            self.assertEqual(chunks, [])
            self.assertEqual(events, [])
            self.assertFalse(listening)
