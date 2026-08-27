from __future__ import annotations

from uuid import UUID

from PySide6 import QtCore, QtWidgets

from alignment_workbench.analysis.tasks import AnalysisCoordinator
from alignment_workbench.state.editor import EditorSession, SessionEvent, SessionEventType
from alignment_workbench.ui.track_widget import DEFAULT_TRACK_HEIGHT, TrackWidget


class TimelineEditor(QtWidgets.QWidget):
    analysis_failed = QtCore.Signal(str)
    PAN_INTERVAL_MS = 16

    def __init__(self, session: EditorSession, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.analysis = AnalysisCoordinator(self)
        self.track_widgets: dict[UUID, TrackWidget] = {}
        self.track_heights: dict[UUID, int] = {}
        self._visible_track_ids: set[UUID] = set()
        self._closed = False
        self._pending_pan_frames = 0.0
        self._scroll_total_frames = -1
        self._scroll_viewport_width = -1
        self._pan_timer = QtCore.QTimer(self)
        self._pan_timer.setSingleShot(True)
        self._pan_timer.setInterval(self.PAN_INTERVAL_MS)
        self._pan_timer.timeout.connect(self._apply_pending_pan)
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
        self.scroller.verticalScrollBar().valueChanged.connect(self._sync_visible_tracks)
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
        self._sync_horizontal_scroll_metrics()
        QtCore.QTimer.singleShot(0, self._sync_visible_tracks)

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
        widget.pan_requested.connect(self._queue_pan)
        widget.pan_finished.connect(self._finish_pan)
        widget.zoom_requested.connect(self._zoom)
        widget.height_changed.connect(
            lambda height, item_id=track.id: self._remember_track_height(item_id, height)
        )
        return widget

    def _state_changed(self, event: SessionEvent) -> None:
        if event.reason is SessionEventType.BATCH:
            self._batch_changed(event)
            return
        if event.reason is SessionEventType.TRACK_ADDED and event.track_id is not None:
            widget = self._create_track_widget(event.track_id)
            index = next(
                index
                for index, track in enumerate(self.session.tracks)
                if track.id == event.track_id
            )
            self.track_layout.insertWidget(index, widget)
            self.track_widgets[event.track_id] = widget
            self._sync_horizontal_scroll_metrics()
        elif event.reason is SessionEventType.TRACK_REMOVED and event.track_id is not None:
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
            self.update_viewport(force_scroll_metrics=True)
        elif event.reason is SessionEventType.TRACK_ORDER:
            for index, track in enumerate(self.session.tracks):
                widget = self.track_widgets[track.id]
                self.track_layout.removeWidget(widget)
                self.track_layout.insertWidget(index, widget)
        elif event.reason is SessionEventType.TRACK_MIX and event.track_id is not None:
            self.track_widgets[event.track_id].sync_mix_controls()
        elif event.reason is SessionEventType.TRACK_LAYOUT:
            widgets = (
                (self.track_widgets[event.track_id],)
                if event.track_id is not None
                else tuple(self.track_widgets.values())
            )
            for widget in widgets:
                widget.sync_layout()
        elif event.reason is SessionEventType.TRACK_CONTENT and event.track_id is not None:
            self.update_viewport(force_scroll_metrics=True)
        elif event.reason is SessionEventType.RENDER_READY and event.track_id is not None:
            target_widget = self.track_widgets.get(event.track_id)
            if target_widget is not None and event.track_id in self._visible_track_ids:
                target_widget.refresh_analysis()
        elif event.reason is SessionEventType.VIEWPORT:
            self.update_viewport()
        elif event.reason is SessionEventType.PLAYHEAD:
            self.update_playhead()
        elif event.reason is SessionEventType.SELECTION:
            self.update_selection()
        elif event.reason is SessionEventType.SEGMENT_SELECTION:
            self.update_playhead()
            self.update_selection()
            self.update_segments()
        elif event.reason is SessionEventType.SEGMENTS and event.track_id is not None:
            target_widget = self.track_widgets.get(event.track_id)
            if target_widget is not None:
                target_widget.update_selection()
                target_widget.update_segments()

    def _batch_changed(self, event: SessionEvent) -> None:
        changes = event.changes
        if SessionEventType.TRACK_REMOVED in changes and event.track_id is not None:
            widget = self.track_widgets.pop(event.track_id, None)
            self.track_heights.pop(event.track_id, None)
            if widget is not None:
                self.track_layout.removeWidget(widget)
                widget.deleteLater()
        if SessionEventType.VIEWPORT in changes or SessionEventType.TRACK_CONTENT in changes:
            self.update_viewport(force_scroll_metrics=SessionEventType.TRACK_CONTENT in changes)
        if SessionEventType.PLAYHEAD in changes:
            self.update_playhead()
        if SessionEventType.SELECTION in changes:
            self.update_selection()
        if SessionEventType.SEGMENT_SELECTION in changes:
            self.update_segments()
        if SessionEventType.TRACK_LAYOUT in changes:
            for widget in self.track_widgets.values():
                widget.sync_layout()

    def update_viewport(self, *, force_scroll_metrics: bool = False) -> None:
        for widget in self._visible_widgets():
            widget.update_viewport()
        metrics_changed = (
            self.session.total_frames != self._scroll_total_frames
            or self.session.viewport.width != self._scroll_viewport_width
        )
        if force_scroll_metrics or metrics_changed:
            self._sync_horizontal_scroll_metrics()
        else:
            self._sync_horizontal_scroll_value()

    def update_playhead(self) -> None:
        for widget in self._visible_widgets():
            widget.update_playhead()

    def update_selection(self) -> None:
        for widget in self._visible_widgets():
            widget.update_selection()

    def update_segments(self) -> None:
        for widget in self._visible_widgets():
            widget.update_segments()

    def _visible_widgets(self) -> tuple[TrackWidget, ...]:
        if not self.isVisible():
            return tuple(self.track_widgets.values())
        return tuple(
            widget
            for track_id, widget in self.track_widgets.items()
            if track_id in self._visible_track_ids
        )

    @QtCore.Slot()
    def _sync_visible_tracks(self) -> set[UUID]:
        viewport = self.scroller.viewport()
        viewport_rect = viewport.rect()
        visible: set[UUID] = set()
        for track_id, widget in self.track_widgets.items():
            top_left = widget.mapTo(viewport, QtCore.QPoint(0, 0))
            rectangle = QtCore.QRect(top_left, widget.size())
            if widget.track.visible and rectangle.intersects(viewport_rect):
                visible.add(track_id)
        newly_visible = visible - self._visible_track_ids
        self._visible_track_ids = visible
        for track_id in newly_visible:
            widget = self.track_widgets[track_id]
            if (
                widget.generation != widget.track.content_version
                and self.session.prepared_render_snapshot(track_id) is not None
            ):
                widget.refresh_analysis()
            widget.update_viewport()
            widget.update_playhead()
            widget.update_selection()
            widget.update_segments()
        return newly_visible

    def _remember_track_height(self, track_id: UUID, height: int) -> None:
        self.track_heights[track_id] = int(height)

    def _sync_horizontal_scroll_metrics(self) -> None:
        maximum = max(0, self.session.total_frames - self.session.viewport.width)
        self._scroll_total_frames = self.session.total_frames
        self._scroll_viewport_width = self.session.viewport.width
        self.horizontal_scroll.blockSignals(True)
        self.horizontal_scroll.setRange(0, maximum)
        self.horizontal_scroll.setPageStep(max(1, self.session.viewport.width))
        self.horizontal_scroll.setSingleStep(max(1, self.session.viewport.width // 10))
        self.horizontal_scroll.setValue(min(self.session.viewport.start, maximum))
        self.horizontal_scroll.blockSignals(False)

    def _sync_horizontal_scroll_value(self) -> None:
        maximum = self.horizontal_scroll.maximum()
        value = min(self.session.viewport.start, maximum)
        if self.horizontal_scroll.value() == value:
            return
        self.horizontal_scroll.blockSignals(True)
        self.horizontal_scroll.setValue(value)
        self.horizontal_scroll.blockSignals(False)

    @QtCore.Slot(float)
    def _queue_pan(self, seconds: float) -> None:
        self._pending_pan_frames += float(seconds) * self.session.sample_rate
        if not self._pan_timer.isActive():
            self._pan_timer.start()

    @QtCore.Slot()
    def _apply_pending_pan(self) -> None:
        requested = round(self._pending_pan_frames)
        self._pending_pan_frames -= requested
        if not requested:
            return
        actual = self.session.pan_viewport(requested)
        if actual != requested:
            self._pending_pan_frames = 0.0

    @QtCore.Slot()
    def _finish_pan(self) -> None:
        self._pan_timer.stop()
        self._apply_pending_pan()
        self._pending_pan_frames = 0.0

    @QtCore.Slot(float, float)
    def _zoom(self, factor: float, anchor_seconds: float) -> None:
        self._finish_pan()
        self.session.zoom_viewport(factor, round(anchor_seconds * self.session.sample_rate))

    @QtCore.Slot(int)
    def _scroll_horizontal(self, frame: int) -> None:
        self._finish_pan()
        self.session.set_viewport(frame, frame + self.session.viewport.width)

    def _set_reference(self, track_id: UUID) -> None:
        self.session.set_reference_track(track_id)

    def _scroll_vertical(self, pixels: int) -> None:
        bar = self.scroller.verticalScrollBar()
        bar.setValue(bar.value() - int(pixels))

    def close(self, wait_ms: int = 0) -> bool:
        if self._closed:
            return True
        self._closed = True
        self._finish_pan()
        self._unsubscribe()
        self.analysis.cancel_all()
        complete = self.analysis.wait(wait_ms)
        if not complete:
            self.analysis.detach_running()
        return complete
