from __future__ import annotations

import numpy as np
from scipy import signal

from src.analysis.formants import estimate_formants
from src.analysis.pitch import estimate_acoustic_tracks
from src.analysis.spectrogram import calculate_spectrogram
from src.analysis.spectrum import calculate_spectrum
from src.fixtures import silence, sine, two_tone

RATE = 16_000


def _peaks(samples: np.ndarray, count: int = 10) -> np.ndarray:
    result = calculate_spectrum(samples, RATE)
    indices = np.argpartition(result.power, -count)[-count:]
    return result.frequencies[indices]


def test_sine_has_peak_near_440_hz() -> None:
    assert np.min(np.abs(_peaks(sine()) - 440)) < 2


def test_two_tone_has_both_peaks() -> None:
    peaks = _peaks(two_tone(), 20)
    assert np.min(np.abs(peaks - 330)) < 2
    assert np.min(np.abs(peaks - 880)) < 2


def test_linear_and_mel_spectrograms_are_finite_and_shaped() -> None:
    for mode in ("linear", "mel"):
        result = calculate_spectrogram(sine(duration=0.1), RATE, mode=mode)
        assert result.decibels.ndim == 2
        assert result.decibels.shape == (len(result.frequencies), len(result.times))
        assert np.isfinite(result.decibels).all()


def test_unvoiced_pitch_is_missing_not_zero() -> None:
    tracks = estimate_acoustic_tracks(silence(), RATE)
    assert np.isnan(tracks.f0_hz).all()
    assert not np.any(tracks.f0_hz == 0)


def test_lpc_formants_recover_three_known_resonances() -> None:
    source = np.zeros(RATE, dtype=np.float64)
    source[:: round(RATE / 120)] = 1.0
    vowel = source
    expected = np.array([500.0, 1_500.0, 2_500.0])
    for frequency, bandwidth in zip(expected, (70.0, 100.0, 140.0), strict=True):
        radius = np.exp(-np.pi * bandwidth / RATE)
        angle = 2 * np.pi * frequency / RATE
        vowel = signal.lfilter(
            [1 - radius],
            [1, -2 * radius * np.cos(angle), radius**2],
            vowel,
        )
    vowel = np.asarray(vowel / np.max(np.abs(vowel)), dtype=np.float32)
    tracks = estimate_formants(vowel, RATE)
    measured = np.nanmedian(tracks.frequencies_hz[:, 10:-10], axis=1)
    np.testing.assert_allclose(measured, expected, atol=100)


def test_silent_formants_are_missing() -> None:
    tracks = estimate_formants(silence(), RATE)
    assert np.isnan(tracks.frequencies_hz).all()


def test_long_spectrum_is_deterministic() -> None:
    samples = two_tone(duration=2)
    first = calculate_spectrum(samples, RATE)
    second = calculate_spectrum(samples.copy(), RATE)
    assert "Welch" in first.method
    np.testing.assert_array_equal(first.power, second.power)
