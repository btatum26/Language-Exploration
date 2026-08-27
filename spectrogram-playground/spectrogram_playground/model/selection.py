from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Selection:
    start: float | None = None
    end: float | None = None

    def set(self, first: float, second: float, duration: float) -> None:
        lower, upper = sorted((float(first), float(second)))
        self.start = min(max(0.0, lower), max(0.0, duration))
        self.end = min(max(self.start, upper), max(0.0, duration))

    def clear(self) -> None:
        self.start = self.end = None

    @property
    def active(self) -> bool:
        return self.start is not None and self.end is not None and self.end > self.start

    @property
    def duration(self) -> float:
        return (self.end - self.start) if self.active else 0.0  # type: ignore[operator]
