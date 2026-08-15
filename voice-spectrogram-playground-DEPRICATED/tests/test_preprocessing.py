"""Tests for stateless preprocessing and local audio validation."""

import numpy as np
import pytest

from src.audio_io import AudioLoadError, load_audio_bytes
from src.preprocessing import resample_audio, to_mono, trim_silence


def test_stereo_converts_to_mono_by_averaging_channels() -> None:
    stereo = np.array([[1.0, -1.0], [0.5, 0.25], [-0.5, 0.5]], dtype=np.float32)
    mono = to_mono(stereo)
    np.testing.assert_allclose(mono, [0.0, 0.375, 0.0])


def test_resampling_has_expected_approximate_length() -> None:
    source = np.zeros(44_100, dtype=np.float32)
    result = resample_audio(source, 44_100, 16_000)
    assert abs(len(result) - 16_000) <= 1


@pytest.mark.parametrize(
    "source",
    (np.zeros(1_000, dtype=np.float32), np.full(1_000, 1e-8, dtype=np.float32)),
)
def test_silence_trimming_handles_silent_and_nearly_silent_audio(source: np.ndarray) -> None:
    trimmed = trim_silence(source)
    assert trimmed.size == 1
    assert np.isfinite(trimmed).all()


def test_invalid_audio_has_a_useful_error() -> None:
    with pytest.raises(AudioLoadError, match="Could not decode invalid.m4a.*FFmpeg"):
        load_audio_bytes(b"this is not audio", "invalid.m4a")

