from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Viewport:
    start: float = 0.0
    end: float = 10.0
    minimum_width: float = 0.01

    @property
    def width(self) -> float:
        return self.end - self.start

    def set(self, start: float, end: float, project_duration: float) -> None:
        duration = max(self.minimum_width, float(project_duration))
        width = min(max(self.minimum_width, float(end) - float(start)), duration)
        new_start = min(max(0.0, float(start)), max(0.0, duration - width))
        self.start, self.end = new_start, new_start + width

    def fit(self, project_duration: float) -> None:
        self.start = 0.0
        self.end = max(self.minimum_width, float(project_duration))

    def zoom(self, factor: float, anchor: float, project_duration: float) -> None:
        if factor <= 0:
            raise ValueError("Zoom factor must be positive")
        ratio = 0.5 if self.width <= 0 else (anchor - self.start) / self.width
        width = self.width * factor
        self.set(anchor - width * ratio, anchor + width * (1 - ratio), project_duration)

    def pan(self, delta: float, project_duration: float) -> None:
        self.set(self.start + delta, self.end + delta, project_duration)
