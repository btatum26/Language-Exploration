from __future__ import annotations

from pathlib import Path

import numpy as np

from spectrogram_playground.model.track import Track

from .resampling import resample_audio, to_mono


class AudioDecodeError(RuntimeError):
    """A user-facing local decoder failure."""


COLORS = ("#59a5d8", "#e07a5f", "#81b29a", "#f2cc8f", "#c77dff", "#ff8fab")


def track_from_samples(
    samples: np.ndarray,
    sample_rate: int,
    *,
    name: str,
    project_rate: int = 48_000,
    analysis_rate: int = 16_000,
    channels: int | None = None,
    source_path: Path | None = None,
    origin: str = "generated",
    color_index: int = 0,
) -> Track:
    values = np.asarray(samples, dtype=np.float32)
    inferred_channels = 1 if values.ndim == 1 else values.shape[1]
    mono = to_mono(values)
    if not mono.size:
        raise AudioDecodeError("The audio source is empty")
    if not np.isfinite(mono).all():
        mono = np.nan_to_num(mono)
    return Track(
        name=name,
        source_samples=values.copy(),
        original_rate=sample_rate,
        channels=channels or inferred_channels,
        playback_samples=resample_audio(mono, sample_rate, project_rate),
        playback_rate=project_rate,
        analysis_samples=resample_audio(mono, sample_rate, analysis_rate),
        analysis_rate=analysis_rate,
        source_path=source_path,
        origin=origin,
        color=COLORS[color_index % len(COLORS)],
    )


def load_track(
    path: str | Path,
    *,
    project_rate: int = 48_000,
    analysis_rate: int = 16_000,
    color_index: int = 0,
) -> Track:
    source = Path(path)
    try:
        import soundfile as sf

        values, rate = sf.read(source, dtype="float32", always_2d=True)
    except Exception as exc:
        suffix = source.suffix.lower()
        hint = (
            " MP3 support depends on the installed libsndfile build; install FFmpeg and "
            "convert the file to WAV, FLAC, or OGG."
            if suffix not in {".wav", ".flac", ".ogg"}
            else " Verify that the file is valid and readable."
        )
        raise AudioDecodeError(f"Could not decode {source.name}.{hint}") from exc
    if values.shape[0] == 0:
        raise AudioDecodeError(f"{source.name} contains no audio frames")
    return track_from_samples(
        values,
        int(rate),
        name=source.stem,
        project_rate=project_rate,
        analysis_rate=analysis_rate,
        channels=values.shape[1],
        source_path=source,
        origin="file",
        color_index=color_index,
    )
