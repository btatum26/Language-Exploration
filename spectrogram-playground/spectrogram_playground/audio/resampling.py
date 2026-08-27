from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import resample_poly


def to_mono(samples: np.ndarray) -> np.ndarray:
    """Average channels while accepting frames-first soundfile arrays."""

    values = np.asarray(samples, dtype=np.float32)
    if values.ndim == 1:
        return values.copy()
    if values.ndim != 2:
        raise ValueError(f"Expected mono or two-dimensional audio, got {values.ndim} dimensions")
    return np.mean(values, axis=1, dtype=np.float32)


def resample_audio(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Sample rates must be positive")
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not values.size:
        return np.zeros(1, dtype=np.float32)
    if source_rate == target_rate:
        return values.copy()
    divisor = gcd(source_rate, target_rate)
    result = resample_poly(values, target_rate // divisor, source_rate // divisor)
    return np.asarray(result, dtype=np.float32)
