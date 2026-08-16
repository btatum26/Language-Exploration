from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.model.track import Track

from .resampling import resample_audio


@dataclass(frozen=True, slots=True)
class AudioClip:
    playback_samples: np.ndarray
    playback_rate: int
    analysis_samples: np.ndarray
    analysis_rate: int

    @property
    def duration(self) -> float:
        return len(self.playback_samples) / self.playback_rate


def _bounds(start: float, end: float, rate: int, length: int) -> tuple[int, int]:
    lower, upper = sorted((float(start), float(end)))
    first = min(length, max(0, round(lower * rate)))
    last = min(length, max(first, round(upper * rate)))
    return first, last


def copy_region(track: Track, start: float, end: float) -> AudioClip:
    playback = track.playback_view
    analysis = track.analysis_view
    first, last = _bounds(start, end, track.playback_rate, len(playback))
    analysis_first, analysis_last = _bounds(start, end, track.analysis_rate, len(analysis))
    if last <= first:
        raise ValueError("The selection does not contain audio on the active track")
    return AudioClip(
        playback[first:last].copy(),
        track.playback_rate,
        analysis[analysis_first:analysis_last].copy(),
        track.analysis_rate,
    )


def delete_region(track: Track, start: float, end: float) -> float:
    playback = track.playback_view
    analysis = track.analysis_view
    first, last = _bounds(start, end, track.playback_rate, len(playback))
    analysis_first, analysis_last = _bounds(start, end, track.analysis_rate, len(analysis))
    if last <= first:
        raise ValueError("The selection does not contain audio on the active track")
    edited_playback = np.concatenate((playback[:first], playback[last:]))
    if not edited_playback.size:
        raise ValueError("Delete the track instead of deleting all of its audio")
    edited_analysis = np.concatenate((analysis[:analysis_first], analysis[analysis_last:]))
    _commit(track, edited_playback, edited_analysis)
    return first / track.playback_rate


def paste_clip(track: Track, position: float, clip: AudioClip) -> tuple[float, float]:
    playback = track.playback_view
    analysis = track.analysis_view
    playback_insert = resample_audio(clip.playback_samples, clip.playback_rate, track.playback_rate)
    analysis_insert = resample_audio(clip.analysis_samples, clip.analysis_rate, track.analysis_rate)
    first, _ = _bounds(position, position, track.playback_rate, len(playback))
    analysis_first, _ = _bounds(position, position, track.analysis_rate, len(analysis))
    edited_playback = np.concatenate((playback[:first], playback_insert, playback[first:]))
    edited_analysis = np.concatenate(
        (analysis[:analysis_first], analysis_insert, analysis[analysis_first:])
    )
    _commit(track, edited_playback, edited_analysis)
    start = first / track.playback_rate
    return start, start + len(playback_insert) / track.playback_rate


def move_region(track: Track, start: float, end: float, destination: float) -> tuple[float, float]:
    clip = copy_region(track, start, end)
    actual_start, actual_end = _bounds(start, end, track.playback_rate, len(track.playback_view))
    start_time = actual_start / track.playback_rate
    end_time = actual_end / track.playback_rate
    target = min(max(0.0, float(destination)), track.duration)
    if start_time <= target <= end_time:
        return start_time, end_time
    delete_region(track, start_time, end_time)
    if target > end_time:
        target -= end_time - start_time
    return paste_clip(track, target, clip)


def _commit(track: Track, playback: np.ndarray, analysis: np.ndarray) -> None:
    track.playback_samples = np.asarray(playback, dtype=np.float32)
    track.analysis_samples = np.asarray(analysis, dtype=np.float32)
    track.trim_start_frame = 0
    track.trim_end_frame = len(track.playback_samples)
