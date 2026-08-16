from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from src.analysis.cache import AnalysisCache
from src.audio.decoding import AudioDecodeError, load_track, track_from_samples


def test_wav_load_preserves_source_and_builds_separate_rates(tmp_path) -> None:
    source = np.column_stack(
        (
            np.linspace(-0.5, 0.5, 800, dtype=np.float32),
            np.linspace(0.5, -0.5, 800, dtype=np.float32),
        )
    )
    path = tmp_path / "stereo.wav"
    sf.write(path, source, 8_000, subtype="FLOAT")
    track = load_track(path, project_rate=48_000, analysis_rate=16_000)
    assert track.original_rate == 8_000
    assert track.channels == 2
    assert track.source_samples.shape == (800, 2)
    assert len(track.playback_samples) == 4_800
    assert len(track.analysis_samples) == 1_600
    assert not np.shares_memory(track.source_samples, track.playback_samples)


def test_invalid_file_has_decoder_or_ffmpeg_guidance(tmp_path) -> None:
    path = tmp_path / "invalid.mp3"
    path.write_bytes(b"not audio")
    with pytest.raises(AudioDecodeError, match="FFmpeg"):
        load_track(path)


def test_empty_samples_are_rejected() -> None:
    with pytest.raises(AudioDecodeError, match="empty"):
        track_from_samples(np.array([], dtype=np.float32), 16_000, name="empty")


def test_analysis_cache_computes_a_key_once() -> None:
    cache = AnalysisCache()
    calls = 0

    def calculate() -> object:
        nonlocal calls
        calls += 1
        return object()

    key = ("track", "stft", 512)
    first = cache.get_or_compute(key, calculate)
    second = cache.get_or_compute(key, calculate)
    assert first is second
    assert calls == 1
