from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

T = TypeVar("T")


class AnalysisCache:
    """Small thread-safe in-memory feature cache keyed by track and settings."""

    def __init__(self) -> None:
        self._values: dict[tuple[object, ...], object] = {}
        self._lock = threading.RLock()

    def key(self, track_id: UUID, feature: str, *settings: object) -> tuple[object, ...]:
        return (track_id, feature, *settings)

    def get(self, key: tuple[object, ...]) -> object | None:
        with self._lock:
            return self._values.get(key)

    def get_or_compute(self, key: tuple[object, ...], factory: Callable[[], T]) -> T:
        with self._lock:
            existing = self._values.get(key)
        if existing is not None:
            return existing  # type: ignore[return-value]
        result = factory()
        with self._lock:
            return self._values.setdefault(key, result)  # type: ignore[return-value]

    def invalidate_track(self, track_id: UUID) -> None:
        with self._lock:
            for key in [key for key in self._values if key[0] == track_id]:
                del self._values[key]

    def discard(self, key: tuple[object, ...]) -> None:
        with self._lock:
            self._values.pop(key, None)

    def discard_prefix(self, *prefix: object) -> None:
        with self._lock:
            for key in [key for key in self._values if key[: len(prefix)] == prefix]:
                del self._values[key]

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
