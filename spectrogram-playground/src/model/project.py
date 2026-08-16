from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from .selection import Selection
from .settings import AnalysisSettings, ToolMode, VisualizationTab
from .track import Track
from .transport import Transport, TransportState
from .viewport import Viewport


@dataclass(slots=True)
class Project:
    playback_rate: int = 48_000
    id: UUID = field(default_factory=uuid4)
    tracks: list[Track] = field(default_factory=list)
    active_track_id: UUID | None = None
    transport: Transport = field(default_factory=Transport)
    selections: dict[UUID, Selection] = field(default_factory=dict)
    _no_track_selection: Selection = field(default_factory=Selection, repr=False)
    viewport: Viewport = field(default_factory=Viewport)
    settings: AnalysisSettings = field(default_factory=AnalysisSettings)
    active_tab: VisualizationTab = VisualizationTab.WAVEFORM
    tool: ToolMode = ToolMode.SEEK

    @property
    def duration(self) -> float:
        return max((track.duration for track in self.tracks), default=0.0)

    @property
    def total_frames(self) -> int:
        return round(self.duration * self.playback_rate)

    @property
    def selection(self) -> Selection:
        if self.active_track_id is None:
            return self._no_track_selection
        return self.selection_for(self.active_track_id)

    def selection_for(self, track_id: UUID) -> Selection:
        return self.selections.setdefault(track_id, Selection())

    def add_track(self, track: Track) -> None:
        if track.playback_rate != self.playback_rate:
            raise ValueError("Track playback rate does not match the project")
        self.tracks.append(track)
        self.selection_for(track.id)
        self.active_track_id = track.id
        if len(self.tracks) == 1:
            self.viewport.fit(self.duration)

    def track_by_id(self, track_id: UUID) -> Track:
        return next(track for track in self.tracks if track.id == track_id)

    def remove_track(self, track_id: UUID) -> Track:
        index = next(i for i, track in enumerate(self.tracks) if track.id == track_id)
        removed = self.tracks.pop(index)
        self.selections.pop(track_id, None)
        if self.active_track_id == track_id:
            self.active_track_id = (
                self.tracks[min(index, len(self.tracks) - 1)].id if self.tracks else None
            )
        self.transport.seek(self.transport.frame_position, self.total_frames)
        self.viewport.set(self.viewport.start, self.viewport.end, self.duration)
        return removed

    def move_track(self, track_id: UUID, delta: int) -> None:
        old = next(i for i, track in enumerate(self.tracks) if track.id == track_id)
        new = min(max(0, old + delta), len(self.tracks) - 1)
        self.tracks.insert(new, self.tracks.pop(old))

    def audible_tracks(self) -> list[Track]:
        soloed = [track for track in self.tracks if track.solo and not track.muted]
        return soloed if soloed else [track for track in self.tracks if not track.muted]

    def stop(self) -> None:
        self.transport.state = TransportState.STOPPED
        target = self.selection.start if self.selection.active else 0.0
        self.transport.seek(round(target * self.playback_rate), self.total_frames)
