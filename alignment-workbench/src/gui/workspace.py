"""Ephemeral workspace placement. No recording snapshot data is modified here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6 import QtCore


@dataclass
class Track:
    recording_id: UUID
    path: Path
    duration: float
    sample_rate: int
    offset: float = 0.0
    muted: bool = False
    solo: bool = False

    def workspace_time(self, sample: int) -> float:
        return self.offset + sample / self.sample_rate

    def local_sample(self, seconds: float) -> int:
        return round(max(0.0, min(self.duration, seconds - self.offset)) * self.sample_rate)


class WorkspaceController(QtCore.QObject):
    changed = QtCore.Signal()
    viewport_changed = QtCore.Signal(float, float)
    playhead_changed = QtCore.Signal(float)
    active_changed = QtCore.Signal(object)
    selection_changed = QtCore.Signal(object)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.tracks: list[Track] = []
        self.active_id: UUID | None = None
        self.viewport = (0.0, 10.0)
        self.playhead = 0.0
        # One selection, owned by a recording and expressed in local samples.
        self.selection: tuple[UUID, int, int] | None = None

    @property
    def duration(self) -> float:
        return max((t.offset + t.duration for t in self.tracks), default=0.0)

    @property
    def audible_tracks(self) -> tuple[Track, ...]:
        solo = any(t.solo for t in self.tracks)
        return tuple(t for t in self.tracks if not t.muted and (not solo or t.solo))

    @property
    def selection_times(self) -> tuple[float, float] | None:
        if self.selection is None:
            return None
        key, start, end = self.selection
        track = self.track(key)
        return track.workspace_time(start), track.workspace_time(end)

    def track(self, key: UUID) -> Track:
        return next(t for t in self.tracks if t.recording_id == key)

    def add(self, track: Track) -> bool:
        if any(t.recording_id == track.recording_id for t in self.tracks):
            self.activate(track.recording_id)
            return False
        self.tracks.append(track)
        self.activate(track.recording_id)
        self.changed.emit()
        self.fit()
        return True

    def remove(self, key: UUID) -> None:
        self.tracks.remove(self.track(key))
        if self.active_id == key:
            self.activate(self.tracks[0].recording_id if self.tracks else None)
        self.changed.emit()

    def activate(self, key: UUID | None) -> None:
        if key is not None:
            self.track(key)
        if self.active_id != key:
            self.active_id = key
            self.select(None)
            self.active_changed.emit(key)

    def select(self, interval: tuple[int, int] | None) -> None:
        self.selection = (
            (self.active_id, *interval) if self.active_id is not None and interval else None
        )
        self.selection_changed.emit(self.selection_times)

    def place(self, key: UUID, offset: float) -> None:
        self.track(key).offset = max(0.0, offset)
        self.changed.emit()
        self.selection_changed.emit(self.selection_times)

    def set_mix(self, key: UUID, *, muted: bool, solo: bool) -> None:
        track = self.track(key)
        track.muted, track.solo = muted, solo
        self.changed.emit()

    def move_order(self, key: UUID, delta: int) -> None:
        track = self.track(key)
        index = self.tracks.index(track)
        self.tracks.pop(index)
        self.tracks.insert(max(0, min(len(self.tracks), index + delta)), track)
        self.changed.emit()

    def set_viewport(self, start: float, end: float) -> None:
        span = max(0.001, end - start)
        self.viewport = (max(0.0, start), max(0.0, start) + span)
        self.viewport_changed.emit(*self.viewport)

    def fit(self) -> None:
        self.set_viewport(0.0, max(0.1, self.duration))

    def zoom(self, factor: float) -> None:
        start, end = self.viewport
        center = (start + end) / 2
        self.set_viewport(center + (start - center) * factor, center + (end - center) * factor)

    def seek(self, seconds: float) -> None:
        self.playhead = max(0.0, min(self.duration, seconds))
        self.playhead_changed.emit(self.playhead)
