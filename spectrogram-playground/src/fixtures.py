from __future__ import annotations

import numpy as np


def sine(frequency: float = 440.0, duration: float = 1.0, rate: int = 16_000) -> np.ndarray:
    time = np.arange(round(duration * rate), dtype=np.float64) / rate
    return np.sin(2 * np.pi * frequency * time).astype(np.float32)


def two_tone(duration: float = 1.0, rate: int = 16_000) -> np.ndarray:
    return (0.55 * sine(330, duration, rate) + 0.45 * sine(880, duration, rate)).astype(np.float32)


def harmonic_series(duration: float = 1.0, rate: int = 16_000) -> np.ndarray:
    result = sum(sine(120 * harmonic, duration, rate) / harmonic for harmonic in range(1, 8))
    return np.asarray(result / np.max(np.abs(result)), dtype=np.float32)


def chirp(duration: float = 1.0, rate: int = 16_000) -> np.ndarray:
    from scipy.signal import chirp as scipy_chirp

    time = np.arange(round(duration * rate), dtype=np.float64) / rate
    return scipy_chirp(time, 100, duration, 3_500).astype(np.float32)


def noise_burst(duration: float = 0.25, rate: int = 16_000) -> np.ndarray:
    return np.random.default_rng(1337).normal(0, 0.2, round(duration * rate)).astype(np.float32)


def silence(duration: float = 1.0, rate: int = 16_000) -> np.ndarray:
    return np.zeros(round(duration * rate), dtype=np.float32)
