"""Tests against signals with known spectral behavior."""

import numpy as np

from src.spectral_analysis import (
    calculate_spectrum,
    estimate_acoustic_tracks,
    linear_spectrogram,
)


SAMPLE_RATE = 16_000


def sine(frequency: float, duration: float = 0.5) -> np.ndarray:
    time = np.arange(round(SAMPLE_RATE * duration)) / SAMPLE_RATE
    return np.sin(2 * np.pi * frequency * time).astype(np.float32)


def strongest_frequencies(samples: np.ndarray, count: int = 5) -> np.ndarray:
    spectrum = calculate_spectrum(samples, SAMPLE_RATE, n_fft=16_384)
    indices = np.argpartition(spectrum.power, -count)[-count:]
    return spectrum.frequencies[indices]


def test_440_hz_sine_has_peak_near_440_hz() -> None:
    peaks = strongest_frequencies(sine(440), count=1)
    assert abs(float(peaks[0]) - 440) < 2


def test_two_tone_signal_has_both_expected_peaks() -> None:
    samples = sine(330) + 0.8 * sine(880)
    peaks = strongest_frequencies(samples, count=12)
    assert np.min(np.abs(peaks - 330)) < 2
    assert np.min(np.abs(peaks - 880)) < 2


def test_very_short_recording_does_not_crash_stft() -> None:
    result = linear_spectrogram(np.array([0.25], dtype=np.float32), SAMPLE_RATE)
    assert result.decibels.ndim == 2
    assert result.decibels.size > 0


def test_spectrogram_contains_only_finite_values() -> None:
    result = linear_spectrogram(sine(440), SAMPLE_RATE)
    assert np.isfinite(result.decibels).all()


def test_unvoiced_f0_is_missing_not_zero() -> None:
    tracks = estimate_acoustic_tracks(np.zeros(SAMPLE_RATE // 2, dtype=np.float32), SAMPLE_RATE)
    assert not np.any(tracks.f0_hz == 0)
    assert np.isnan(tracks.f0_hz[~tracks.voiced]).all()

