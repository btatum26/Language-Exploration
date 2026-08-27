from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal


@dataclass(frozen=True, slots=True)
class SpectrumResult:
    frequencies: np.ndarray
    magnitude: np.ndarray
    power: np.ndarray
    decibels: np.ndarray
    method: str


def calculate_spectrum(
    samples: np.ndarray,
    sample_rate: int,
    *,
    long_threshold_seconds: float = 1.0,
    window: str = "hann",
    n_fft: int = 16_384,
) -> SpectrumResult:
    values = np.nan_to_num(np.asarray(samples, dtype=np.float32).reshape(-1))
    if not values.size:
        values = np.zeros(1, dtype=np.float32)
    if len(values) / sample_rate <= long_threshold_seconds:
        fft_size = max(int(n_fft), 2 ** int(np.ceil(np.log2(max(2, len(values))))))
        tapered = values * signal.get_window(window, len(values), fftbins=True)
        coefficients = np.fft.rfft(tapered, n=fft_size)
        magnitude = np.abs(coefficients) / max(1, len(values))
        power = magnitude**2
        frequencies = np.fft.rfftfreq(fft_size, 1 / sample_rate)
        method = f"{window.title()}-window FFT"
    else:
        segment = min(len(values), max(256, round(0.25 * sample_rate)))
        frequencies, power = signal.welch(
            values,
            fs=sample_rate,
            window=window,
            nperseg=segment,
            noverlap=segment // 2,
            nfft=max(n_fft, segment),
            detrend=False,
            scaling="spectrum",
        )
        magnitude = np.sqrt(power)
        method = f"Welch averaged spectrum ({window} window)"
    decibels = 10 * np.log10(np.maximum(power, np.finfo(float).tiny))
    return SpectrumResult(
        np.asarray(frequencies),
        np.asarray(magnitude),
        np.asarray(power),
        np.asarray(decibels),
        method,
    )
