from __future__ import annotations

from uuid import UUID

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from src.model.project import Project
from src.model.track import Track

from .linked_view import InteractivePlot, add_timeline_items
from .track_header import TrackHeader


class TrackWorkspace(QtWidgets.QScrollArea):
    seek_requested = QtCore.Signal(float)
    selection_requested = QtCore.Signal(object, float, float)
    pan_requested = QtCore.Signal(float)
    zoom_requested = QtCore.Signal(float, float)
    changed = QtCore.Signal()
    remove_requested = QtCore.Signal(object)
    move_requested = QtCore.Signal(object, int)
    activated = QtCore.Signal(object)

    def __init__(self, project: Project, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.container = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.container)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(2)
        self.layout.addStretch()
        self.setWidget(self.container)
        self.setWidgetResizable(True)
        self.plots: dict[UUID, InteractivePlot] = {}
        self.headers: dict[UUID, TrackHeader] = {}
        self.playheads: dict[UUID, pg.InfiniteLine] = {}
        self.regions: dict[UUID, pg.LinearRegionItem] = {}

    def clear_lanes(self) -> None:
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.plots.clear()
        self.headers.clear()
        self.playheads.clear()
        self.regions.clear()

    def rebuild(self) -> None:
        self.clear_lanes()
        for track in self.project.tracks:
            self._add_lane(track)
        if not self.plots:
            label = QtWidgets.QLabel("Add an audio track to begin")
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(180)
            self.layout.insertWidget(0, label)
        self.update_timeline()

    def _add_lane(self, track: Track) -> None:
        lane = QtWidgets.QWidget()
        lane.setMinimumHeight(145)
        row = QtWidgets.QHBoxLayout(lane)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        header = TrackHeader(track, track.id == self.project.active_track_id)
        header.changed.connect(self.changed)
        header.remove_requested.connect(self.remove_requested)
        header.move_requested.connect(self.move_requested)
        header.activated.connect(self.activated)
        plot = InteractivePlot()
        plot.seek_requested.connect(self.seek_requested)
        plot.selection_requested.connect(
            lambda first, second, track_id=track.id: self.selection_requested.emit(
                track_id, first, second
            )
        )
        plot.pan_requested.connect(self.pan_requested)
        plot.zoom_requested.connect(self.zoom_requested)
        plot.setLabel("bottom", "Time", units="s")
        if track.visible:
            self.populate_plot(plot, track)
        else:
            plot.setVisible(False)
            lane.setMinimumHeight(96)
        playhead, region = add_timeline_items(plot)
        self.plots[track.id], self.playheads[track.id], self.regions[track.id] = (
            plot,
            playhead,
            region,
        )
        self.headers[track.id] = header
        row.addWidget(header)
        row.addWidget(plot, 1)
        self.layout.insertWidget(self.layout.count() - 1, lane)

    def populate_plot(self, plot: InteractivePlot, track: Track) -> None:
        raise NotImplementedError

    def update_playhead(self, position: float) -> None:
        for playhead in self.playheads.values():
            playhead.setPos(position)

    def update_timeline(self, position: float | None = None) -> None:
        if position is None:
            position = self.project.transport.frame_position / self.project.playback_rate
        self.update_playhead(position)
        for track_id, region in self.regions.items():
            selection = self.project.selection_for(track_id)
            if selection.active:
                region.setRegion((selection.start, selection.end))
                region.setVisible(True)
            else:
                region.setVisible(False)
        for plot in self.plots.values():
            plot.setXRange(self.project.viewport.start, self.project.viewport.end, padding=0)

    def set_active_track(self, track_id: UUID) -> None:
        for candidate_id, header in self.headers.items():
            header.set_active(candidate_id == track_id)

    def x_ranges(self) -> list[tuple[float, float]]:
        return [tuple(plot.viewRange()[0]) for plot in self.plots.values()]
