from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np


@dataclass(slots=True)
class Track:
    """One immutable-source clip, beginning at project time zero."""

    name: str
    source_samples: np.ndarray
    original_rate: int
    channels: int
    playback_samples: np.ndarray
    playback_rate: int
    analysis_samples: np.ndarray
    analysis_rate: int
    source_path: Path | None = None
    origin: str = "file"
    id: UUID = field(default_factory=uuid4)
    color: str = "#59a5d8"
    gain: float = 1.0
    muted: bool = False
    solo: bool = False
    visible: bool = True
    amplitude_scale: float = 1.0
    trim_start_frame: int = 0
    trim_end_frame: int | None = None

    def __post_init__(self) -> None:
        if self.original_rate <= 0 or self.playback_rate <= 0 or self.analysis_rate <= 0:
            raise ValueError("Sample rates must be positive")
        if self.channels <= 0:
            raise ValueError("Channel count must be positive")
        self.source_samples = np.asarray(self.source_samples, dtype=np.float32)
        self.playback_samples = np.asarray(self.playback_samples, dtype=np.float32).reshape(-1)
        self.analysis_samples = np.asarray(self.analysis_samples, dtype=np.float32).reshape(-1)
        self.gain = max(0.0, float(self.gain))
        self.trim_start_frame = min(
            max(0, int(self.trim_start_frame)), max(0, len(self.playback_samples) - 1)
        )
        if self.trim_end_frame is None:
            self.trim_end_frame = len(self.playback_samples)
        self.trim_end_frame = min(
            max(self.trim_start_frame + 1, int(self.trim_end_frame)),
            len(self.playback_samples),
        )

    @property
    def playback_view(self) -> np.ndarray:
        return self.playback_samples[self.trim_start_frame : self.trim_end_frame]

    @property
    def analysis_view(self) -> np.ndarray:
        start_seconds = self.trim_start_frame / self.playback_rate
        end_seconds = self.trim_end_frame / self.playback_rate
        start = min(len(self.analysis_samples), round(start_seconds * self.analysis_rate))
        end = min(
            len(self.analysis_samples),
            max(start + 1, round(end_seconds * self.analysis_rate)),
        )
        return self.analysis_samples[start:end]

    @property
    def full_duration(self) -> float:
        return len(self.playback_samples) / self.playback_rate

    @property
    def duration(self) -> float:
        return (self.trim_end_frame - self.trim_start_frame) / self.playback_rate

    @property
    def is_trimmed(self) -> bool:
        return self.trim_start_frame > 0 or self.trim_end_frame < len(self.playback_samples)

    @property
    def trim_bounds_seconds(self) -> tuple[float, float]:
        return (
            self.trim_start_frame / self.playback_rate,
            self.trim_end_frame / self.playback_rate,
        )

    def trim(self, start_seconds: float, end_seconds: float) -> None:
        """Keep a region relative to the current trimmed view and rebase it to zero."""

        duration = self.duration
        start = min(max(0.0, float(start_seconds)), duration)
        end = min(max(start, float(end_seconds)), duration)
        if end - start < 1 / self.playback_rate:
            raise ValueError("A trim must retain at least one playback frame")
        current_start = self.trim_start_frame
        new_start = current_start + round(start * self.playback_rate)
        new_end = current_start + round(end * self.playback_rate)
        self.trim_start_frame = min(new_start, len(self.playback_samples) - 1)
        self.trim_end_frame = min(
            len(self.playback_samples), max(self.trim_start_frame + 1, new_end)
        )

    def reset_trim(self) -> None:
        self.trim_start_frame = 0
        self.trim_end_frame = len(self.playback_samples)

    @property
    def metadata_summary(self) -> str:
        rates = f"{self.original_rate / 1000:g}→{self.playback_rate / 1000:g} kHz"
        trim = (
            f" · trim {self.trim_start_frame / self.playback_rate:.2f}–"
            f"{self.trim_end_frame / self.playback_rate:.2f}s"
            if self.is_trimmed
            else ""
        )
        return f"{self.duration:.2f}s · {self.channels}ch · {rates}{trim}"
