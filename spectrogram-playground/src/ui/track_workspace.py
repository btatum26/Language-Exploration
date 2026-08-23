from __future__ import annotations

from collections.abc import MutableMapping
from uuid import UUID

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from src.model.project import Project
from src.model.track import Track

from .linked_view import InteractivePlot, add_timeline_items
from .track_header import TrackHeader

MIN_TRACK_HEIGHT = 145
DEFAULT_TRACK_HEIGHT = 210
RESIZE_HANDLE_HEIGHT = 8


class TrackResizeHandle(QtWidgets.QWidget):
    """Bottom-edge drag target for changing one lane's preferred height."""

    def __init__(self, lane: TrackLane) -> None:
        super().__init__(lane)
        self.lane = lane
        self._start_y: float | None = None
        self._start_height = 0
        self.setFixedHeight(RESIZE_HANDLE_HEIGHT)
        self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag to resize this track")
        self.setAccessibleName("Resize track height")

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(QtGui.QColor("#59606a"), 1))
        y = self.rect().center().y()
        painter.drawLine(0, y, self.width(), y)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._start_y = event.globalPosition().y()
            self._start_height = self.lane.preferred_height
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._start_y is not None:
            delta = round(event.globalPosition().y() - self._start_y)
            self.lane.set_preferred_height(self._start_height + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._start_y is not None:
            self._start_y = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TrackLane(QtWidgets.QWidget):
    height_changed = QtCore.Signal(int)

    def __init__(self, preferred_height: int) -> None:
        super().__init__()
        self.preferred_height = max(MIN_TRACK_HEIGHT, int(preferred_height))
        self.setFixedHeight(self.preferred_height)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.content = QtWidgets.QWidget()
        self.row = QtWidgets.QHBoxLayout(self.content)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(2)
        self.resize_handle = TrackResizeHandle(self)
        self.layout.addWidget(self.content, 1)
        self.layout.addWidget(self.resize_handle)

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        return QtCore.QSize(hint.width(), self.preferred_height)

    def set_preferred_height(self, height: int, *, notify: bool = True) -> None:
        height = max(MIN_TRACK_HEIGHT, int(height))
        if height == self.preferred_height:
            return
        self.preferred_height = height
        self.setFixedHeight(height)
        if self.parentWidget() is not None and self.parentWidget().layout() is not None:
            self.parentWidget().layout().activate()
        if notify:
            self.height_changed.emit(height)


class TrackWorkspace(QtWidgets.QScrollArea):
    seek_requested = QtCore.Signal(float)
    selection_requested = QtCore.Signal(object, float, float)
    pan_requested = QtCore.Signal(float)
    zoom_requested = QtCore.Signal(float, float)
    changed = QtCore.Signal()
    remove_requested = QtCore.Signal(object)
    move_requested = QtCore.Signal(object, int)
    activated = QtCore.Signal(object)
    lane_height_changed = QtCore.Signal(object, int)

    def __init__(
        self,
        project: Project,
        parent: QtWidgets.QWidget | None = None,
        lane_heights: MutableMapping[UUID, int] | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.lane_heights = lane_heights if lane_heights is not None else {}
        self.container = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.container)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(2)
        self.layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinAndMaxSize)
        self.layout.addStretch()
        self.setWidget(self.container)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.lanes: dict[UUID, TrackLane] = {}
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
        self.lanes.clear()
        self.headers.clear()
        self.playheads.clear()
        self.regions.clear()

    def rebuild(self) -> None:
        self.clear_lanes()
        track_ids = {track.id for track in self.project.tracks}
        for track_id in set(self.lane_heights) - track_ids:
            del self.lane_heights[track_id]
        for track in self.project.tracks:
            self._add_lane(track)
        if not self.plots:
            label = QtWidgets.QLabel("Add an audio track to begin")
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(180)
            self.layout.insertWidget(0, label)
        self.update_timeline()

    def _add_lane(self, track: Track) -> None:
        lane = TrackLane(self.lane_heights.get(track.id, DEFAULT_TRACK_HEIGHT))
        lane.height_changed.connect(
            lambda height, track_id=track.id: self._remember_lane_height(track_id, height)
        )
        row = lane.row
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
        plot.vertical_scroll_requested.connect(self.scroll_tracks)
        plot.setLabel("bottom", "Time", units="s")
        if track.visible:
            self.populate_plot(plot, track)
        else:
            plot.setVisible(False)
        playhead, region = add_timeline_items(plot)
        self.plots[track.id], self.playheads[track.id], self.regions[track.id] = (
            plot,
            playhead,
            region,
        )
        self.headers[track.id] = header
        self.lanes[track.id] = lane
        row.addWidget(header)
        row.addWidget(plot, 1)
        self.layout.insertWidget(self.layout.count() - 1, lane)

    def _remember_lane_height(self, track_id: UUID, height: int) -> None:
        self.lane_heights[track_id] = height
        self.lane_height_changed.emit(track_id, height)

    @QtCore.Slot(object, int)
    def set_lane_height(self, track_id: UUID, height: int) -> None:
        self.lane_heights[track_id] = max(MIN_TRACK_HEIGHT, int(height))
        lane = self.lanes.get(track_id)
        if lane is not None:
            lane.set_preferred_height(height, notify=False)

    @QtCore.Slot(int)
    def scroll_tracks(self, delta: int) -> None:
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() - delta)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            angle_delta = event.angleDelta().y() or event.angleDelta().x()
            pixel_delta = event.pixelDelta().y() or event.pixelDelta().x()
            wheel_steps = angle_delta / 120 if angle_delta else pixel_delta / 60
            self.pan_requested.emit(-wheel_steps * self.project.viewport.width * 0.1)
            event.accept()
            return
        super().wheelEvent(event)

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
