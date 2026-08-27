from __future__ import annotations

from spectrogram_playground.model.project import Project

from .linked_view import InteractivePlot, add_timeline_items


class TimelineRuler(InteractivePlot):
    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.setFixedHeight(58)
        self.hideAxis("left")
        self.setYRange(0, 1)
        self.playhead, self.region = add_timeline_items(self)

    def update_playhead(self, position: float) -> None:
        self.playhead.setPos(position)

    def refresh(self, position: float | None = None) -> None:
        self.setXRange(self.project.viewport.start, self.project.viewport.end, padding=0)
        if position is None:
            position = self.project.transport.frame_position / self.project.playback_rate
        self.update_playhead(position)
        self.region.setVisible(self.project.selection.active)
        if self.project.selection.active:
            self.region.setRegion((self.project.selection.start, self.project.selection.end))
