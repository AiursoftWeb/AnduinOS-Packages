"""Deterministic public noise stimuli only; no backend implementation."""
from array import array
import math
import random
import sys


def noise(shape, dbfs, seconds=15):
    rng = random.Random(42)
    values = []
    filtered = 0.0
    impulse_left = 0
    next_impulse = 1600
    for index in range(int(seconds * 16000)):
        if shape == 'hum':
            value = math.sin(2 * math.pi * 50 * index / 16000) + 0.4 * math.sin(2 * math.pi * 100 * index / 16000)
        elif shape == 'fan-like':
            filtered = 0.98 * filtered + 0.02 * rng.gauss(0, 1)
            value = filtered + 0.03 * rng.gauss(0, 1)
        elif shape == 'tapping':
            if index == next_impulse:
                impulse_left = 800
                next_impulse += rng.randint(2400, 8000)
            value = rng.gauss(0, 1) * math.exp(-(800-impulse_left)/128) if impulse_left else 0
            impulse_left = max(0, impulse_left - 1)
        else:
            raise ValueError('Unknown noise shape')
        values.append(value)
    rms = math.sqrt(sum(x*x for x in values) / len(values))
    scale = 32768 * 10 ** (dbfs/20) / rms
    result = array('h', (max(-32768, min(32767, round(x * scale))) for x in values))
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()



if __name__ == "__main__":
    sys.stdout.buffer.write(noise(sys.argv[1], int(sys.argv[2])))
