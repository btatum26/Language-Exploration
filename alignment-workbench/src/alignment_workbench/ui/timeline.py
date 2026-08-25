from __future__ import annotations

from uuid import UUID

from PySide6 import QtCore, QtWidgets

from alignment_workbench.analysis.tasks import AnalysisCoordinator
from alignment_workbench.state.editor import EditorSession, SessionEvent
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
        self.track_layout = QtWidgets.QVBoxLayout(self.content)
        self.track_layout.setContentsMargins(0, 0, 0, 0)
        self.track_layout.setSpacing(4)
        self.track_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinAndMaxSize)
        self.track_layout.addStretch(1)
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
        while self.track_layout.count() > 1:
            item = self.track_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        track_ids = {track.id for track in self.session.tracks}
        for track_id in set(self.track_heights) - track_ids:
            del self.track_heights[track_id]
        for track in self.session.tracks:
            widget = self._create_track_widget(track.id)
            self.track_layout.insertWidget(self.track_layout.count() - 1, widget)
            self.track_widgets[track.id] = widget
        self._sync_horizontal_scroll()

    def _create_track_widget(self, track_id: UUID) -> TrackWidget:
        track = self.session.track(track_id)
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
            lambda height, item_id=track.id: self._remember_track_height(item_id, height)
        )
        return widget

    def _state_changed(self, event: SessionEvent) -> None:
        if event.reason == "track-added" and event.track_id is not None:
            widget = self._create_track_widget(event.track_id)
            index = next(
                index
                for index, track in enumerate(self.session.tracks)
                if track.id == event.track_id
            )
            self.track_layout.insertWidget(index, widget)
            self.track_widgets[event.track_id] = widget
            self._sync_horizontal_scroll()
        elif event.reason == "track-removed" and event.track_id is not None:
            widget = (
                self.track_widgets.pop(event.track_id)
                if event.track_id in self.track_widgets
                else None
            )
            self.track_heights.pop(event.track_id, None)
            if widget is not None:
                self.track_layout.removeWidget(widget)
                widget.deleteLater()
            for item in self.track_widgets.values():
                item.sync_layout()
            self._sync_horizontal_scroll()
        elif event.reason == "track-order":
            for index, track in enumerate(self.session.tracks):
                widget = self.track_widgets[track.id]
                self.track_layout.removeWidget(widget)
                self.track_layout.insertWidget(index, widget)
        elif event.reason == "track-mix" and event.track_id is not None:
            self.track_widgets[event.track_id].sync_mix_controls()
        elif event.reason == "track-layout":
            widgets = (
                (self.track_widgets[event.track_id],)
                if event.track_id is not None
                else tuple(self.track_widgets.values())
            )
            for widget in widgets:
                widget.sync_layout()
        elif event.reason == "track-content" and event.track_id is not None:
            target_widget = self.track_widgets.get(event.track_id)
            if target_widget is not None:
                target_widget.refresh_analysis()
            self.update_timeline()
        elif event.reason in {"timeline", "viewport", "segment", "segments"}:
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
        self.session.set_reference_track(track_id)

    def _scroll_vertical(self, pixels: int) -> None:
        bar = self.scroller.verticalScrollBar()
        bar.setValue(bar.value() - int(pixels))

    def close(self) -> None:
        self._unsubscribe()
        self.analysis.wait(30_000)
