from __future__ import annotations

from uuid import UUID

from PySide6 import QtCore, QtWidgets

from alignment_workbench.analysis.tasks import AnalysisCoordinator
from alignment_workbench.state.editor import EditorSession
from alignment_workbench.ui.track_widget import DEFAULT_TRACK_HEIGHT, TrackWidget


class TimelineEditor(QtWidgets.QWidget):
    analysis_failed = QtCore.Signal(str)

    def __init__(self, session: EditorSession, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.analysis = AnalysisCoordinator(self)
        self.track_widgets: dict[UUID, TrackWidget] = {}
        self.track_heights: dict[UUID, int] = {}
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroller = QtWidgets.QScrollArea()
        self.scroller.setWidgetResizable(True)
        self.scroller.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroller.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.content)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        self.layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinAndMaxSize)
        self.layout.addStretch(1)
        self.scroller.setWidget(self.content)
        root.addWidget(self.scroller, 1)
        self.horizontal_scroll = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
        self.horizontal_scroll.setAccessibleName("Timeline horizontal position")
        self.horizontal_scroll.setToolTip("Scroll the shared timeline left or right")
        self.horizontal_scroll.valueChanged.connect(self._scroll_horizontal)
        root.addWidget(self.horizontal_scroll)
        self._unsubscribe = session.subscribe(self._state_changed)
        self.rebuild()

    def rebuild(self) -> None:
        for widget in self.track_widgets.values():
            widget.deleteLater()
        self.track_widgets.clear()
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        track_ids = {track.id for track in self.session.tracks}
        for track_id in set(self.track_heights) - track_ids:
            del self.track_heights[track_id]
        for track in self.session.tracks:
            widget = TrackWidget(
                self.session,
                track,
                self.analysis,
                preferred_height=self.track_heights.get(track.id, DEFAULT_TRACK_HEIGHT),
            )
            widget.remove_requested.connect(self.session.remove_track)
            widget.move_requested.connect(self.session.reorder_track)
            widget.reference_requested.connect(self._set_reference)
            widget.analysis_failed.connect(self.analysis_failed)
            widget.vertical_scroll_requested.connect(self._scroll_vertical)
            widget.height_changed.connect(
                lambda height, track_id=track.id: self._remember_track_height(track_id, height)
            )
            self.layout.insertWidget(self.layout.count() - 1, widget)
            self.track_widgets[track.id] = widget
        self._sync_horizontal_scroll()

    def _state_changed(self, reason: str) -> None:
        if reason == "tracks":
            self.rebuild()
        elif reason == "clips":
            for track_id, widget in self.track_widgets.items():
                if track_id == self.session.active_track_id:
                    widget.refresh_analysis()
            self.update_timeline()
        elif reason in {"timeline", "viewport", "segment", "segments", "display"}:
            self.update_timeline()

    def update_timeline(self) -> None:
        for widget in self.track_widgets.values():
            widget.update_timeline()
        self._sync_horizontal_scroll()

    def _remember_track_height(self, track_id: UUID, height: int) -> None:
        self.track_heights[track_id] = int(height)

    def _sync_horizontal_scroll(self) -> None:
        maximum = max(0, self.session.total_frames - self.session.viewport.width)
        self.horizontal_scroll.blockSignals(True)
        self.horizontal_scroll.setRange(0, maximum)
        self.horizontal_scroll.setPageStep(max(1, self.session.viewport.width))
        self.horizontal_scroll.setSingleStep(max(1, self.session.viewport.width // 10))
        self.horizontal_scroll.setValue(min(self.session.viewport.start, maximum))
        self.horizontal_scroll.blockSignals(False)

    @QtCore.Slot(int)
    def _scroll_horizontal(self, frame: int) -> None:
        self.session.viewport.set(
            frame,
            frame + self.session.viewport.width,
            self.session.total_frames,
        )
        self.session._emit("viewport")

    def _set_reference(self, track_id: UUID) -> None:
        self.session.reference_track_id = track_id
        self.session._emit("tracks")

    def _scroll_vertical(self, pixels: int) -> None:
        bar = self.scroller.verticalScrollBar()
        bar.setValue(bar.value() - int(pixels))

    def close(self) -> None:
        self._unsubscribe()
        self.analysis.wait(30_000)
