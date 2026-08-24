from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FrameSelection:
    start: int | None = None
    end: int | None = None

    @property
    def active(self) -> bool:
        return self.start is not None and self.end is not None and self.end > self.start

    @property
    def duration_frames(self) -> int:
        if not self.active:
            return 0
        assert self.start is not None and self.end is not None
        return self.end - self.start

    def set(self, first: int, second: int, maximum: int) -> None:
        start, end = sorted((int(first), int(second)))
        self.start = min(max(0, start), max(0, maximum))
        self.end = min(max(self.start, end), max(0, maximum))

    def clear(self) -> None:
        self.start = self.end = None


@dataclass(slots=True)
class FrameViewport:
    start: int = 0
    end: int = 480_000
    minimum_width: int = 480

    @property
    def width(self) -> int:
        return self.end - self.start

    def set(self, start: int, end: int, project_frames: int) -> None:
        project = max(self.minimum_width, int(project_frames))
        width = min(max(self.minimum_width, int(end) - int(start)), project)
        actual_start = min(max(0, int(start)), max(0, project - width))
        self.start, self.end = actual_start, actual_start + width

    def fit(self, project_frames: int) -> None:
        self.start = 0
        self.end = max(self.minimum_width, int(project_frames))

    def zoom(self, factor: float, anchor: int, project_frames: int) -> None:
        if factor <= 0:
            raise ValueError("Zoom factor must be positive")
        ratio = 0.5 if self.width <= 0 else (int(anchor) - self.start) / self.width
        width = max(self.minimum_width, round(self.width * factor))
        self.set(round(anchor - width * ratio), round(anchor + width * (1 - ratio)), project_frames)

    def pan(self, delta: int, project_frames: int) -> None:
        self.set(self.start + int(delta), self.end + int(delta), project_frames)
