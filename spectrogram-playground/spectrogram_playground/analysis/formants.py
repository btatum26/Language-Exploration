from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np
from scipy import signal

from .spectrogram import _finite_audio, frame_lengths


@dataclass(frozen=True, slots=True)
class FormantTracks:
    times: np.ndarray
    frequencies_hz: np.ndarray

    @property
    def f1_hz(self) -> np.ndarray:
        return self.frequencies_hz[0]

    @property
    def f2_hz(self) -> np.ndarray:
        return self.frequencies_hz[1]

    @property
    def f3_hz(self) -> np.ndarray:
        return self.frequencies_hz[2]


def estimate_formants(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    lpc_order: int | None = None,
    pre_emphasis: float = 0.97,
    maximum_bandwidth_hz: float = 700.0,
) -> FormantTracks:
    """Estimate experimental F1-F3 tracks from LPC spectral-envelope roots."""

    values = _finite_audio(samples)
    frame_length, hop_length = frame_lengths(sample_rate, window_ms, hop_ms)
    order = lpc_order or max(8, round(sample_rate / 1_000) - 4)
    order = min(order, frame_length - 2)
    padded = np.pad(values, frame_length // 2, mode="constant")
    frame_count = max(1, 1 + (len(padded) - frame_length) // hop_length)
    frequencies = np.full((3, frame_count), np.nan, dtype=float)
    upper_frequency = min(5_000.0, sample_rate / 2 - 50.0)
    window = signal.windows.hann(frame_length, sym=False)

    for index in range(frame_count):
        frame = padded[index * hop_length : index * hop_length + frame_length]
        if len(frame) < frame_length or float(np.sqrt(np.mean(frame**2))) < 1e-4:
            continue
        emphasized = signal.lfilter([1.0, -pre_emphasis], [1.0], frame) * window
        try:
            roots = np.roots(librosa.lpc(emphasized, order=order))
        except (ValueError, FloatingPointError, np.linalg.LinAlgError):
            continue
        roots = roots[np.imag(roots) > 0]
        candidate_frequencies = np.angle(roots) * sample_rate / (2 * np.pi)
        bandwidths = -sample_rate / np.pi * np.log(np.maximum(np.abs(roots), 1e-12))
        valid = (
            (candidate_frequencies >= 90.0)
            & (candidate_frequencies <= upper_frequency)
            & (bandwidths > 0.0)
            & (bandwidths <= maximum_bandwidth_hz)
        )
        candidates = np.sort(candidate_frequencies[valid])
        count = min(3, len(candidates))
        frequencies[:count, index] = candidates[:count]

    times = librosa.frames_to_time(np.arange(frame_count), sr=sample_rate, hop_length=hop_length)
    return FormantTracks(times, frequencies)
