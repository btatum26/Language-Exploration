from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class WaveformEnvelope:
    times: np.ndarray
    minimum: np.ndarray
    maximum: np.ndarray


def create_envelope(
    samples: np.ndarray, sample_rate: int, *, maximum_columns: int = 20_000
) -> WaveformEnvelope:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not values.size:
        values = np.zeros(1, dtype=np.float32)
    bucket = max(1, int(np.ceil(len(values) / maximum_columns)))
    padded = np.pad(values, (0, (-len(values)) % bucket), mode="edge")
    blocks = padded.reshape(-1, bucket)
    times = np.arange(len(blocks), dtype=float) * bucket / sample_rate
    return WaveformEnvelope(times, blocks.min(axis=1), blocks.max(axis=1))
