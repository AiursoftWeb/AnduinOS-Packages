"""Deterministic in-memory fixture augmentation, never microphone capture."""
from array import array
import math
import random
import sys


def noisy(pcm, snr_db=20):
    if not pcm or len(pcm) % 2:
        raise ValueError("Expected nonempty S16LE PCM")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    rms = math.sqrt(sum(x*x for x in samples) / len(samples))
    generator = random.Random(42)
    sigma = rms / 10 ** (snr_db / 20)
    result = array("h", (max(-32768, min(32767, round(x + generator.gauss(0, sigma))))
                         for x in samples))
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()
