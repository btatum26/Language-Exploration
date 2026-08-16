from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np


@dataclass(frozen=True, slots=True)
class SpectrogramResult:
    frequencies: np.ndarray
    times: np.ndarray
    decibels: np.ndarray
    mode: str


def frame_lengths(sample_rate: int, window_ms: float, hop_ms: float) -> tuple[int, int]:
    return max(2, round(sample_rate * window_ms / 1000)), max(1, round(sample_rate * hop_ms / 1000))


def _finite_audio(samples: np.ndarray) -> np.ndarray:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    return np.nan_to_num(values) if values.size else np.zeros(1, dtype=np.float32)


def calculate_spectrogram(
    samples: np.ndarray,
    sample_rate: int,
    *,
    mode: str = "linear",
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    n_fft: int = 512,
    dynamic_range_db: float = 80.0,
    n_mels: int = 80,
    fmin: float = 0.0,
    fmax: float | None = None,
) -> SpectrogramResult:
    values = _finite_audio(samples)
    win_length, hop_length = frame_lengths(sample_rate, window_ms, hop_ms)
    n_fft = max(int(n_fft), win_length)
    high = min(sample_rate / 2, fmax if fmax is not None else sample_rate / 2)

    if mode == "mel":
        power = librosa.feature.melspectrogram(
            y=values,
            sr=sample_rate,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window="hann",
            center=True,
            pad_mode="constant",
            n_mels=n_mels,
            fmin=max(0.0, fmin),
            fmax=high,
            power=2.0,
        )
        reference = max(float(np.max(power, initial=0.0)), np.finfo(float).tiny)
        decibels = librosa.power_to_db(power, ref=reference, top_db=dynamic_range_db)
        frequencies = librosa.mel_frequencies(n_mels=n_mels, fmin=max(0.0, fmin), fmax=high)

    elif mode == "linear":
        transformed = librosa.stft(
            values,
            n_fft=n_fft,
            hop_length=hop_length,
            win_length=win_length,
            window="hann",
            center=True,
            pad_mode="constant",
        )
        magnitude = np.abs(transformed)
        reference = max(float(np.max(magnitude, initial=0.0)), np.finfo(float).tiny)
        decibels = librosa.amplitude_to_db(magnitude, ref=reference, top_db=dynamic_range_db)
        frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)

    else:
        raise ValueError(f"Unsupported spectrogram mode: {mode}")

    times = librosa.frames_to_time(
        np.arange(decibels.shape[1]), sr=sample_rate, hop_length=hop_length
    )
    finite = np.nan_to_num(decibels, nan=-dynamic_range_db, neginf=-dynamic_range_db)
    return SpectrogramResult(frequencies, times, finite, mode)
