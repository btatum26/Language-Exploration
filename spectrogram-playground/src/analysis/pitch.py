from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .spectrogram import _finite_audio, frame_lengths


@dataclass(frozen=True, slots=True)
class AcousticTracks:
    times: np.ndarray
    f0_hz: np.ndarray
    rms: np.ndarray


def estimate_acoustic_tracks(
    samples: np.ndarray,
    sample_rate: int,
    *,
    window_ms: float = 25.0,
    hop_ms: float = 10.0,
    fmin: float = 65.0,
    fmax: float = 500.0,
) -> AcousticTracks:
    values = _finite_audio(samples)
    frame_length, hop_length = frame_lengths(sample_rate, window_ms, hop_ms)
    frame_length = max(frame_length, int(np.ceil(2.1 * sample_rate / fmin)), 32)
    if np.max(np.abs(values), initial=0.0) < 1e-7:
        count = max(1, 1 + len(values) // hop_length)
        f0 = np.full(count, np.nan)
    else:
        try:
            f0, voiced, _ = librosa.pyin(
                values,
                sr=sample_rate,
                fmin=fmin,
                fmax=min(fmax, sample_rate / 2 - 1),
                frame_length=frame_length,
                hop_length=hop_length,
                center=True,
                fill_na=np.nan,
            )
            f0 = np.asarray(f0, dtype=float)
            f0[~np.asarray(voiced, dtype=bool)] = np.nan
        except (ValueError, librosa.util.exceptions.ParameterError):
            count = max(1, 1 + len(values) // hop_length)
            f0 = np.full(count, np.nan)
    rms = librosa.feature.rms(
        y=values,
        frame_length=frame_length,
        hop_length=hop_length,
        center=True,
        pad_mode="constant",
    )[0]
    count = min(len(f0), len(rms))
    times = librosa.frames_to_time(np.arange(count), sr=sample_rate, hop_length=hop_length)
    return AcousticTracks(times, f0[:count], np.asarray(rms[:count]))
