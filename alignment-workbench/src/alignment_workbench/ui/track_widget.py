from __future__ import annotations

from uuid import UUID

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from src.ui.linked_view import InteractivePlot, add_timeline_items

from alignment_workbench.analysis.tasks import AnalysisCoordinator, TrackAnalysis
from alignment_workbench.state.editor import (
    DisplayMode,
    EditorSession,
    EditorTrack,
    Segment,
    ToolMode,
)
from alignment_workbench.ui.segment_tier import SegmentTier

MIN_TRACK_HEIGHT = 220
DEFAULT_TRACK_HEIGHT = 315
RESIZE_HANDLE_HEIGHT = 8
PLOT_LEFT_AXIS_WIDTH = 52


class TrackResizeHandle(QtWidgets.QWidget):
    """Bottom-edge drag target for changing one track's fixed height."""

    def __init__(self, track_widget: TrackWidget) -> None:
        super().__init__(track_widget)
        self.track_widget = track_widget
        self._start_y: float | None = None
        self._start_height = 0
        self.setFixedHeight(RESIZE_HANDLE_HEIGHT)
        self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag to resize this track")
        self.setAccessibleName("Resize track height")

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(QtGui.QColor("#59606a"), 1))
        y = self.rect().center().y()
        painter.drawLine(0, y, self.width(), y)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._start_y = event.globalPosition().y()
            self._start_height = self.track_widget.preferred_height
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._start_y is not None:
            delta = round(event.globalPosition().y() - self._start_y)
            self.track_widget.set_preferred_height(self._start_height + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._start_y is not None:
            self._start_y = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class EditorPlot(InteractivePlot):
    editor_drag_started = QtCore.Signal(float)
    editor_drag_moved = QtCore.Signal(float)
    editor_drag_finished = QtCore.Signal(float)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.editor_drag_started.emit(self.time_at(event.position().toPoint()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self.editor_drag_moved.emit(self.time_at(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.editor_drag_finished.emit(self.time_at(event.position().toPoint()))
        super().mouseReleaseEvent(event)


class TrackWidget(QtWidgets.QFrame):
    remove_requested = QtCore.Signal(object)
    move_requested = QtCore.Signal(object, int)
    reference_requested = QtCore.Signal(object)
    analysis_failed = QtCore.Signal(str)
    vertical_scroll_requested = QtCore.Signal(int)
    height_changed = QtCore.Signal(int)

    def __init__(
        self,
        session: EditorSession,
        track: EditorTrack,
        analysis: AnalysisCoordinator,
        preferred_height: int = DEFAULT_TRACK_HEIGHT,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.track = track
        self.analysis = analysis
        self.generation = 0
        self._editor_drag_start: int | None = None
        self._moving_clip_id: UUID | None = None
        self.setObjectName("trackWidget")
        self.preferred_height = max(MIN_TRACK_HEIGHT, int(preferred_height))
        self.setFixedHeight(self.preferred_height)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(row)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(1)
        root.addWidget(self._build_header())

        self.analysis_container = QtWidgets.QWidget()
        stack = QtWidgets.QVBoxLayout(self.analysis_container)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(1)
        self.waveform = EditorPlot()
        self.waveform.setObjectName("waveformLane")
        self.waveform.setYRange(-1.05, 1.05, padding=0)
        self.waveform.getAxis("left").setWidth(PLOT_LEFT_AXIS_WIDTH)
        self.waveform.hideAxis("bottom")
        self.spectrogram = EditorPlot()
        self.spectrogram.setObjectName("spectrogramLane")
        self.spectrogram.setYRange(0, 8_000, padding=0)
        self.spectrogram.getAxis("left").setWidth(PLOT_LEFT_AXIS_WIDTH)
        self.spectrogram.hideAxis("bottom")
        self.tier = SegmentTier(session, track)
        stack.addWidget(self.waveform, 1)
        stack.addWidget(self.spectrogram, 1)
        stack.addWidget(self.tier, 0)
        root.addWidget(self.analysis_container, 1)
        outer.addWidget(row, 1)
        self.resize_handle = TrackResizeHandle(self)
        outer.addWidget(self.resize_handle)

        for plot in (self.waveform, self.spectrogram):
            plot.seek_requested.connect(self._seek_seconds)
            plot.selection_requested.connect(self._select_seconds)
            plot.pan_requested.connect(self._pan_seconds)
            plot.zoom_requested.connect(self._zoom_seconds)
            plot.vertical_scroll_requested.connect(self.vertical_scroll_requested)
            plot.editor_drag_started.connect(self._editor_drag_started)
            plot.editor_drag_moved.connect(self._editor_drag_moved)
            plot.editor_drag_finished.connect(self._editor_drag_finished)
        self.tier.segment_selected.connect(self._select_segment)
        self.tier.boundary_changed.connect(self._boundary_changed)
        self.analysis.completed.connect(self._analysis_ready)
        self.analysis.failed.connect(self._analysis_failed)
        self.apply_display_mode()
        self.analysis_container.setVisible(self.track.visible)
        self.refresh_analysis()
        QtCore.QTimer.singleShot(0, self._sync_tier_geometry)

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

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self._sync_tier_geometry)

    def _sync_tier_geometry(self) -> None:
        plot = self.waveform if self.waveform.isVisible() else self.spectrogram
        view_rect = plot.plotItem.vb.sceneBoundingRect()
        left = plot.mapFromScene(view_rect.topLeft()).x()
        right = plot.mapFromScene(view_rect.topRight()).x()
        self.tier.set_timeline_geometry(left, max(1, right - left))

    def _build_header(self) -> QtWidgets.QWidget:
        header = QtWidgets.QFrame()
        header.setObjectName("trackHeader")
        header.setFixedWidth(190)
        layout = QtWidgets.QVBoxLayout(header)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(4)
        self.name = QtWidgets.QLineEdit(self.track.name)
        self.name.editingFinished.connect(self._rename)
        layout.addWidget(self.name)
        speaker = self.track.speaker_id or "No speaker"
        self.metadata = QtWidgets.QLabel(f"{speaker} · {self.track.language}")
        self.metadata.setObjectName("trackMetadata")
        layout.addWidget(self.metadata)
        modes = QtWidgets.QHBoxLayout()
        self.mode_group = QtWidgets.QButtonGroup(self)
        for label, mode in (
            ("Both", DisplayMode.BOTH),
            ("Wave", DisplayMode.WAVE),
            ("Spec", DisplayMode.SPEC),
        ):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(self.track.display_mode is mode)
            button.clicked.connect(lambda _checked=False, value=mode: self._set_mode(value))
            self.mode_group.addButton(button)
            modes.addWidget(button)
        layout.addLayout(modes)
        toggles = QtWidgets.QHBoxLayout()
        self.mix_buttons: dict[str, QtWidgets.QToolButton] = {}
        for label, attr in (("M", "muted"), ("S", "solo"), ("Show", "visible")):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setChecked(bool(getattr(self.track, attr)))
            button.toggled.connect(
                lambda checked, field=attr: self._set_track_boolean(field, checked)
            )
            self.mix_buttons[attr] = button
            toggles.addWidget(button)
        layout.addLayout(toggles)
        gain_row = QtWidgets.QHBoxLayout()
        gain_row.addWidget(QtWidgets.QLabel("Gain"))
        self.gain_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.gain_slider.setRange(0, 200)
        self.gain_slider.setValue(round(self.track.gain * 100))
        self.gain_slider.valueChanged.connect(self._set_gain)
        gain_row.addWidget(self.gain_slider)
        layout.addLayout(gain_row)
        controls = QtWidgets.QHBoxLayout()
        self.reference_button = QtWidgets.QToolButton()
        self.reference_button.setText("Ref")
        self.reference_button.setCheckable(True)
        self.reference_button.setChecked(self.session.reference_track_id == self.track.id)
        self.reference_button.clicked.connect(lambda: self.reference_requested.emit(self.track.id))
        controls.addWidget(self.reference_button)
        for label, delta in (("↑", -1), ("↓", 1)):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.clicked.connect(
                lambda _checked=False, amount=delta: self.move_requested.emit(self.track.id, amount)
            )
            controls.addWidget(button)
        remove = QtWidgets.QToolButton()
        remove.setText("×")
        remove.clicked.connect(lambda: self.remove_requested.emit(self.track.id))
        controls.addWidget(remove)
        layout.addLayout(controls)
        self.details = QtWidgets.QLabel("")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        layout.addStretch(1)
        return header

    def refresh_analysis(self) -> None:
        self.generation += 1
        self.analysis.invalidate(self.track.id)
        samples = self.session.render_track(self.track.id)
        source = self.session.sources[self.track.clips[0].source_id]
        self.details.setText(
            f"{len(samples) / self.session.sample_rate:.2f}s · "
            f"{source.original_rate / 1000:g} kHz · {source.channels}ch · "
            f"{len(self.track.clips)} clip(s)"
        )
        for plot, label in (
            (self.waveform, "Preparing waveform…"),
            (self.spectrogram, "Computing…"),
        ):
            plot.clear()
            plot.addItem(pg.TextItem(label, color="#aeb4bc", anchor=(0, 0)))
        self.analysis.analyze(
            self.track.id, self.generation, samples.copy(), self.session.sample_rate
        )
        self.update_timeline()

    @QtCore.Slot(object, int, object)
    def _analysis_ready(self, track_id: UUID, generation: int, result: TrackAnalysis) -> None:
        if track_id != self.track.id or generation != self.generation:
            return
        self.waveform.clear()
        low = self.waveform.plot(
            result.waveform.times, result.waveform.minimum, pen=pg.mkPen(self.track.color)
        )
        high = self.waveform.plot(
            result.waveform.times, result.waveform.maximum, pen=pg.mkPen(self.track.color)
        )
        self.waveform.addItem(
            pg.FillBetweenItem(low, high, brush=pg.mkBrush(self.track.color + "55"))
        )
        self.spectrogram.clear()
        image = pg.ImageItem(axisOrder="row-major")
        image.setImage(result.spectrogram.decibels, autoLevels=False, levels=(-80, 0))
        image.setLookupTable(pg.colormap.get("inferno").getLookupTable(nPts=256))
        duration = max(
            0.001,
            float(result.spectrogram.times[-1]) if len(result.spectrogram.times) else 0.001,
        )
        maximum = float(result.spectrogram.frequencies[-1])
        image.setRect(QtCore.QRectF(0, 0, duration, maximum))
        self.spectrogram.addItem(image)
        self.spectrogram.setYRange(0, min(8_000, maximum), padding=0)
        self._wave_playhead, self._wave_region = add_timeline_items(self.waveform)
        self._spec_playhead, self._spec_region = add_timeline_items(self.spectrogram)
        self.update_timeline()

    @QtCore.Slot(object, int, str)
    def _analysis_failed(self, track_id: UUID, generation: int, message: str) -> None:
        if track_id == self.track.id and generation == self.generation:
            self.analysis_failed.emit(message)

    def update_timeline(self) -> None:
        start = self.session.viewport.start / self.session.sample_rate
        end = self.session.viewport.end / self.session.sample_rate
        playhead = self.session.playhead_frame / self.session.sample_rate
        active = self.session.selection.active
        selection = (
            (
                self.session.selection.start / self.session.sample_rate,
                self.session.selection.end / self.session.sample_rate,
            )
            if active
            else (0, 0)
        )
        for plot in (self.waveform, self.spectrogram):
            plot.setXRange(start, end, padding=0)
        for line in (getattr(self, "_wave_playhead", None), getattr(self, "_spec_playhead", None)):
            if line is not None:
                line.setValue(playhead)
        for region in (getattr(self, "_wave_region", None), getattr(self, "_spec_region", None)):
            if region is not None:
                region.setRegion(selection)
                region.setVisible(active)
        self.tier.update()

    def apply_display_mode(self) -> None:
        self.waveform.setVisible(self.track.display_mode in {DisplayMode.BOTH, DisplayMode.WAVE})
        self.spectrogram.setVisible(self.track.display_mode in {DisplayMode.BOTH, DisplayMode.SPEC})
        self.mode_group.buttons()[list(DisplayMode).index(self.track.display_mode)].setChecked(True)
        QtCore.QTimer.singleShot(0, self._sync_tier_geometry)

    def sync_mix_controls(self) -> None:
        for field in ("muted", "solo"):
            blocker = QtCore.QSignalBlocker(self.mix_buttons[field])
            self.mix_buttons[field].setChecked(bool(getattr(self.track, field)))
            del blocker
        blocker = QtCore.QSignalBlocker(self.gain_slider)
        self.gain_slider.setValue(round(self.track.gain * 100))
        del blocker

    def sync_layout(self) -> None:
        if not self.name.hasFocus():
            self.name.setText(self.track.name)
        blocker = QtCore.QSignalBlocker(self.mix_buttons["visible"])
        self.mix_buttons["visible"].setChecked(self.track.visible)
        del blocker
        self.analysis_container.setVisible(self.track.visible)
        self.reference_button.setChecked(self.session.reference_track_id == self.track.id)
        self.apply_display_mode()

    def _set_mode(self, mode: DisplayMode) -> None:
        self.session.set_display_mode(self.track.id, mode)
        self.apply_display_mode()

    def _rename(self) -> None:
        value = self.name.text().strip()
        if value:
            self.session.set_track_name(self.track.id, value)
            self.name.setText(self.track.name)
        else:
            self.name.setText(self.track.name)

    def _set_track_boolean(self, field: str, checked: bool) -> None:
        if field == "visible":
            self.session.set_track_visible(self.track.id, checked)
        elif field == "muted":
            self.session.set_track_mix(self.track.id, muted=checked)
        elif field == "solo":
            self.session.set_track_mix(self.track.id, solo=checked)

    def _set_gain(self, value: int) -> None:
        self.session.set_track_mix(self.track.id, gain=value / 100)

    def _seek_seconds(self, seconds: float) -> None:
        if self.session.tool is not ToolMode.SEEK:
            return
        self.session.active_track_id = self.track.id
        self.session.set_playhead(round(seconds * self.session.sample_rate))

    def _editor_drag_started(self, seconds: float) -> None:
        frame = round(seconds * self.session.sample_rate)
        self.session.active_track_id = self.track.id
        self._editor_drag_start = frame
        if self.session.tool is ToolMode.SELECT:
            self.session.set_playhead(frame)
            self.session.set_selection(frame, frame)
        elif self.session.tool is ToolMode.MOVE:
            clip = next(
                (
                    item
                    for item in self.track.clips
                    if item.timeline_start_frame <= frame < item.timeline_end_frame
                ),
                None,
            )
            self._moving_clip_id = clip.id if clip is not None else None
        elif self.session.tool is ToolMode.SPLIT:
            try:
                self.session.split_at(frame)
            except ValueError:
                pass

    def _editor_drag_moved(self, seconds: float) -> None:
        if self._editor_drag_start is None:
            return
        frame = round(seconds * self.session.sample_rate)
        if self.session.tool is ToolMode.SELECT:
            self.session.set_selection(self._editor_drag_start, frame)

    def _editor_drag_finished(self, seconds: float) -> None:
        if self._editor_drag_start is None:
            return
        frame = round(seconds * self.session.sample_rate)
        if self.session.tool is ToolMode.SELECT:
            self.session.set_selection(self._editor_drag_start, frame)
        elif self.session.tool is ToolMode.MOVE and self._moving_clip_id is not None:
            self.session.move_clip(self._moving_clip_id, frame - self._editor_drag_start)
        self._editor_drag_start = None
        self._moving_clip_id = None

    def _select_seconds(self, first: float, second: float) -> None:
        self.session.active_track_id = self.track.id
        self.session.set_selection(
            round(first * self.session.sample_rate), round(second * self.session.sample_rate)
        )

    def _pan_seconds(self, seconds: float) -> None:
        self.session.viewport.pan(
            round(seconds * self.session.sample_rate), self.session.total_frames
        )
        self.session._emit("viewport")

    def _zoom_seconds(self, factor: float, anchor: float) -> None:
        self.session.viewport.zoom(
            factor, round(anchor * self.session.sample_rate), self.session.total_frames
        )
        self.session._emit("viewport")

    def _select_segment(self, segment_id: str) -> None:
        self.session.select_segment(self.track.id, segment_id)

    def _boundary_changed(self, segment_id: str, start: int, end: int) -> None:
        segment = self.session.segment(self.track.id, segment_id)
        self.session.update_segment(
            self.track.id,
            Segment(
                id=segment.id,
                kind=segment.kind,
                label=segment.label,
                start_frame=start,
                end_frame=end,
                parent_id=segment.parent_id,
                confidence=segment.confidence,
                review_state=segment.review_state,
                model_label=segment.model_label,
                model_start_frame=segment.model_start_frame,
                model_end_frame=segment.model_end_frame,
                provenance=segment.provenance,
                effective_revision_id=segment.effective_revision_id,
                model_segment_id=segment.model_segment_id,
                saved_label=segment.saved_label,
                saved_start_frame=segment.saved_start_frame,
                saved_end_frame=segment.saved_end_frame,
            ),
        )
