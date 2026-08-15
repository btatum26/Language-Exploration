"""Stateless audio preprocessing; callers retain the original recording."""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np


@dataclass(frozen=True)
class ProcessingResult:
    samples: np.ndarray
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate


def to_mono(samples: np.ndarray) -> np.ndarray:
    """Average channels without changing an already mono signal."""

    values = np.asarray(samples, dtype=np.float32)
    if values.ndim == 1:
        return values.copy()
    if values.ndim != 2:
        raise ValueError(f"Expected one or two dimensions, got {values.ndim}.")
    if values.shape[0] <= 8 and values.shape[1] > values.shape[0]:
        values = values.T
    return np.mean(values, axis=1, dtype=np.float32)


def resample_audio(samples: np.ndarray, original_rate: int, target_rate: int) -> np.ndarray:
    """Resample audio and preserve its approximate duration."""

    if original_rate <= 0 or target_rate <= 0:
        raise ValueError("Sample rates must be positive.")
    values = np.asarray(samples, dtype=np.float32)
    if original_rate == target_rate:
        return values.copy()
    return np.asarray(
        librosa.resample(values, orig_sr=original_rate, target_sr=target_rate, axis=0),
        dtype=np.float32,
    )


def trim_silence(samples: np.ndarray, top_db: float = 40.0) -> np.ndarray:
    """Remove quiet edges while returning a safe sample for silent input."""

    values = np.asarray(samples, dtype=np.float32)
    if values.size == 0 or float(np.max(np.abs(values), initial=0.0)) < 1e-5:
        return np.zeros(1, dtype=np.float32)
    mono = to_mono(values)
    _, bounds = librosa.effects.trim(mono, top_db=top_db)
    start, end = (int(bounds[0]), int(bounds[1]))
    if end <= start:
        return np.zeros(1, dtype=np.float32)
    return values[start:end].copy()


def normalize_audio(samples: np.ndarray, mode: str) -> np.ndarray:
    """Apply peak or RMS normalization, or leave the signal unchanged."""

    values = np.asarray(samples, dtype=np.float32)
    if mode == "None":
        return values.copy()
    if mode == "Peak":
        scale = float(np.max(np.abs(values), initial=0.0))
        target = 0.98
    elif mode == "RMS":
        scale = float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))
        target = 0.1
    else:
        raise ValueError(f"Unknown normalization mode: {mode}")
    if scale <= np.finfo(np.float32).eps:
        return values.copy()
    return np.asarray(values * (target / scale), dtype=np.float32)


def preprocess_audio(
    samples: np.ndarray,
    sample_rate: int,
    *,
    mono: bool = True,
    target_rate: int | None = 16_000,
    trim: bool = False,
    normalization: str = "None",
    silence_top_db: float = 40.0,
) -> ProcessingResult:
    """Run requested operations once from the unchanged source samples."""

    processed = to_mono(samples) if mono else np.asarray(samples, dtype=np.float32).copy()
    if not mono and processed.ndim == 2:
        if processed.shape[0] <= 8 and processed.shape[1] > processed.shape[0]:
            processed = processed.T
        processed = processed[:, 0]  # One channel is still required for scalar analysis.
    if trim:
        processed = trim_silence(processed, silence_top_db)
    output_rate = sample_rate if target_rate is None else target_rate
    processed = resample_audio(processed, sample_rate, output_rate)
    processed = normalize_audio(processed, normalization)
    return ProcessingResult(processed, output_rate)


def select_region(
    samples: np.ndarray, sample_rate: int, start_seconds: float, end_seconds: float
) -> np.ndarray:
    """Return a non-empty, bounded time selection."""

    values = np.asarray(samples, dtype=np.float32)
    start = max(0, min(len(values) - 1, int(round(start_seconds * sample_rate))))
    end = max(start + 1, min(len(values), int(round(end_seconds * sample_rate))))
    return values[start:end].copy()
