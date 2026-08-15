"""Fourier, time-frequency, pitch, and optional formant analysis."""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np
from scipy import signal


@dataclass(frozen=True)
class SpectrumResult:
    frequencies: np.ndarray
    magnitude: np.ndarray
    power: np.ndarray
    method: str


@dataclass(frozen=True)
class SpectrogramResult:
    frequencies: np.ndarray
    times: np.ndarray
    decibels: np.ndarray


@dataclass(frozen=True)
class AcousticTracks:
    times: np.ndarray
    f0_hz: np.ndarray
    voiced: np.ndarray
    rms: np.ndarray
    centroid_hz: np.ndarray


def frame_lengths(sample_rate: int, window_ms: float, hop_ms: float) -> tuple[int, int]:
    """Convert millisecond controls to positive sample counts."""

    return max(2, round(sample_rate * window_ms / 1_000)), max(1, round(sample_rate * hop_ms / 1_000))


def _mono_finite(samples: np.ndarray) -> np.ndarray:
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size == 0:
        values = np.zeros(1, dtype=np.float32)
    return np.nan_to_num(values)


def safe_stft(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    n_fft: int = 512,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculate a Hann-window STFT, including for extremely short inputs."""

    values = _mono_finite(samples)
    window_length, hop_length = frame_lengths(sample_rate, window_ms, hop_ms)
    n_fft = max(int(n_fft), window_length)
    transformed = librosa.stft(
        values,
        n_fft=n_fft,
        hop_length=hop_length,
        win_length=window_length,
        window="hann",
        center=True,
        pad_mode="constant",
    )
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=n_fft)
    times = librosa.frames_to_time(np.arange(transformed.shape[1]), sr=sample_rate, hop_length=hop_length)
    return transformed, frequencies, times


def linear_spectrogram(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    n_fft: int = 512,
    dynamic_range_db: float = 80.0,
) -> SpectrogramResult:
    """Return a finite, peak-relative linear-frequency spectrogram in dB."""

    transformed, frequencies, times = safe_stft(
        samples, sample_rate, window_ms=window_ms, hop_ms=hop_ms, n_fft=n_fft
    )
    magnitude = np.abs(transformed)
    reference = max(float(np.max(magnitude, initial=0.0)), np.finfo(float).tiny)
    decibels = librosa.amplitude_to_db(magnitude, ref=reference, top_db=dynamic_range_db)
    return SpectrogramResult(frequencies, times, np.nan_to_num(decibels, nan=-dynamic_range_db))


def mel_spectrogram(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    n_fft: int = 512,
    n_mels: int = 80,
    fmin: float = 0.0,
    fmax: float | None = None,
    dynamic_range_db: float = 80.0,
) -> SpectrogramResult:
    """Return a finite log-Mel power spectrogram."""

    values = _mono_finite(samples)
    window_length, hop_length = frame_lengths(sample_rate, window_ms, hop_ms)
    n_fft = max(n_fft, window_length)
    maximum = min(sample_rate / 2, fmax if fmax is not None else sample_rate / 2)
    power = librosa.feature.melspectrogram(
        y=values,
        sr=sample_rate,
        n_fft=n_fft,
        hop_length=hop_length,
        win_length=window_length,
        window="hann",
        center=True,
        pad_mode="constant",
        n_mels=n_mels,
        fmin=max(0.0, fmin),
        fmax=maximum,
        power=2.0,
    )
    reference = max(float(np.max(power, initial=0.0)), np.finfo(float).tiny)
    decibels = librosa.power_to_db(power, ref=reference, top_db=dynamic_range_db)
    times = librosa.frames_to_time(np.arange(power.shape[1]), sr=sample_rate, hop_length=hop_length)
    frequencies = librosa.mel_frequencies(n_mels=n_mels, fmin=max(0.0, fmin), fmax=maximum)
    return SpectrogramResult(frequencies, times, np.nan_to_num(decibels, nan=-dynamic_range_db))


def calculate_spectrum(
    samples: np.ndarray,
    sample_rate: int,
    *,
    long_threshold_seconds: float = 1.0,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    n_fft: int = 512,
) -> SpectrumResult:
    """Use a windowed FFT for short selections and averaged STFT power for long ones."""

    values = _mono_finite(samples)
    if len(values) / sample_rate <= long_threshold_seconds:
        fft_size = max(n_fft, int(2 ** np.ceil(np.log2(max(2, len(values))))))
        padded = np.zeros(fft_size, dtype=np.float32)
        tapered = values * signal.windows.hann(len(values), sym=False)
        padded[: len(values)] = tapered
        coefficients = np.fft.rfft(padded)
        magnitude = np.abs(coefficients) / max(1, len(values))
        power = np.square(magnitude)
        frequencies = np.fft.rfftfreq(fft_size, 1 / sample_rate)
        method = "Windowed FFT of the selected region"
    else:
        transformed, frequencies, _ = safe_stft(
            values, sample_rate, window_ms=window_ms, hop_ms=hop_ms, n_fft=n_fft
        )
        power = np.mean(np.abs(transformed) ** 2, axis=1)
        magnitude = np.sqrt(power)
        method = "Average short-time power spectrum"
    return SpectrumResult(frequencies, np.asarray(magnitude), np.asarray(power), method)


def estimate_acoustic_tracks(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    fmin: float = 65.0,
    fmax: float = 500.0,
) -> AcousticTracks:
    """Estimate F0, voicing, RMS energy, and spectral centroid on one time grid."""

    values = _mono_finite(samples)
    frame_length, hop_length = frame_lengths(sample_rate, window_ms, hop_ms)
    minimum_pitch_frame = int(np.ceil(2.05 * sample_rate / fmin))
    frame_length = max(frame_length, minimum_pitch_frame, 2 * hop_length, 32)
    try:
        f0, voiced, _ = librosa.pyin(
            values,
            fmin=fmin,
            fmax=min(fmax, sample_rate / 2 - 1),
            sr=sample_rate,
            frame_length=frame_length,
            hop_length=hop_length,
            center=True,
            fill_na=np.nan,
            pad_mode="constant",
        )
    except (ValueError, librosa.util.exceptions.ParameterError):
        frame_count = 1 + len(values) // hop_length
        f0 = np.full(frame_count, np.nan)
        voiced = np.zeros(frame_count, dtype=bool)
    rms = librosa.feature.rms(
        y=values, frame_length=frame_length, hop_length=hop_length, center=True, pad_mode="constant"
    )[0]
    centroid = librosa.feature.spectral_centroid(
        y=values, sr=sample_rate, n_fft=frame_length, hop_length=hop_length, center=True
    )[0]
    count = min(len(f0), len(rms), len(centroid))
    f0_values = np.asarray(f0[:count], dtype=float)
    voiced_values = np.asarray(voiced[:count], dtype=bool) & np.isfinite(f0_values)
    f0_values[~voiced_values] = np.nan
    times = librosa.frames_to_time(np.arange(count), sr=sample_rate, hop_length=hop_length)
    return AcousticTracks(times, f0_values, voiced_values, rms[:count], centroid[:count])


def estimate_formants(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Estimate the first three formants per frame with LPC; failures remain NaN."""

    values = _mono_finite(samples)
    frame_length, hop_length = frame_lengths(sample_rate, window_ms, hop_ms)
    order = max(8, min(20, 2 + sample_rate // 1_000))
    padded = np.pad(values, frame_length // 2)
    frame_count = max(1, 1 + (len(padded) - frame_length) // hop_length)
    output = np.full((3, frame_count), np.nan)
    for index in range(frame_count):
        frame = padded[index * hop_length : index * hop_length + frame_length]
        if len(frame) < frame_length or np.sqrt(np.mean(frame**2)) < 1e-4:
            continue
        emphasized = signal.lfilter([1, -0.97], [1], frame) * signal.windows.hann(frame_length)
        try:
            roots = np.roots(librosa.lpc(emphasized, order=order))
        except (ValueError, FloatingPointError, np.linalg.LinAlgError):
            continue
        roots = roots[np.imag(roots) >= 0]
        frequencies = np.angle(roots) * sample_rate / (2 * np.pi)
        bandwidths = -0.5 * sample_rate / np.pi * np.log(np.maximum(np.abs(roots), 1e-12))
        candidates = np.sort(frequencies[(frequencies > 90) & (frequencies < 5_000) & (bandwidths < 500)])
        output[: min(3, len(candidates)), index] = candidates[:3]
    times = librosa.frames_to_time(np.arange(frame_count), sr=sample_rate, hop_length=hop_length)
    return times, output
