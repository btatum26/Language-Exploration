"""Track presentation and adapter for the existing recording editor and render workers."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from PySide6 import QtCore, QtGui, QtWidgets

from gui.annotation_inspector import AnnotationInspector
from gui.controller import ApplicationClient
from gui.main_window import MainWindow as RecordingEditor
from gui.tasks import TaskSubmitter
from gui.track_stack import STANDARD_TRACK_HEIGHT, TrackResizeHandle
from gui.workspace import Track, WorkspaceController
from models import SignalAnnotation


class TrackEditor(RecordingEditor):
    """Reuse the tested edit form/actions without a table or independent audio source.

    The reference shell stays hidden; its form and renderer are reparented into
    the workstation. Selection is an annotation ID, never a hidden table row.
    """

    inspector_binding: AnnotationInspector

    def _set_audio_source(self, path: Path) -> None:
        pass  # The workspace owns the only playback source.

    def _seek_sample(self, sample: int) -> None:
        # Playback is routed by the track widget to the shared workspace player.
        self.start_sample.setValue(sample)

    def _refresh_annotation_table(self, session: object) -> None:
        current = self.controller.session
        if current and self._selected_annotation_id is not None:
            self._populate_editor(current.get_annotation(self._selected_annotation_id))
        self.waveform.set_selected_annotation(self._selected_annotation_id)

    def _select_annotation(self, value: object) -> None:
        if isinstance(value, UUID) and self.controller.session is not None:
            self._selected_annotation_id = value
            self._populate_editor(self.controller.session.get_annotation(value))
            self.waveform.set_selected_annotation(value)
            self._update_actions()

    def _update_actions(self) -> None:
        super()._update_actions()
        selected = self._selected_annotation_id is not None
        self.concept_combo.setEnabled(not self._busy and self.controller.session is not None)
        self.concept_search.setEnabled(not self._busy)
        self.category_filter.setEnabled(not self._busy)
        self.editor_mode.setText("Edit annotation" if selected else "Create annotation")
        self.use_core_button.hide()

    def _populate_editor(self, annotation: SignalAnnotation) -> None:
        super()._populate_editor(annotation)


class ClipHeader(QtWidgets.QWidget):
    moved = QtCore.Signal(float)
    activated = QtCore.Signal()

    def __init__(self, workspace: WorkspaceController, track: Track) -> None:
        super().__init__()
        self.workspace, self.track = workspace, track
        self.setFixedHeight(26)
        self.setCursor(QtCore.Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag this clip header to align the recording")
        self._press: float | None = None
        self._offset = 0.0

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#15191f"))
        start, end = self.workspace.viewport
        scale = (self.width() - 20) / (end - start)
        rect = QtCore.QRectF(
            10 + (self.track.offset - start) * scale, 2, self.track.duration * scale, 22
        )
        painter.fillRect(rect, QtGui.QColor("#3b4d60"))
        painter.setPen(QtGui.QColor("#edf2f8"))
        painter.drawText(
            rect.adjusted(6, 0, -6, 0),
            QtCore.Qt.AlignmentFlag.AlignVCenter,
            f"⠿  Clip  ·  {self.track.offset:.3f} s",
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.activated.emit()
            self._press = event.position().x()
            self._offset = self.track.offset

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press is not None:
            start, end = self.workspace.viewport
            self.moved.emit(
                self._offset
                + (event.position().x() - self._press) * (end - start) / max(1, self.width() - 20)
            )

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self.mouseMoveEvent(event)
        self._press = None


class TrackWidget(QtWidgets.QFrame):
    remove_requested = QtCore.Signal(object)
    inspector_changed = QtCore.Signal()
    marker_requested = QtCore.Signal()
    vertical_scroll_requested = QtCore.Signal(int)

    def __init__(self, editor: TrackEditor, track: Track, workspace: WorkspaceController) -> None:
        super().__init__()
        self.editor, self.track, self.workspace = editor, track, workspace
        self.setObjectName("track")
        self.setFixedHeight(STANDARD_TRACK_HEIGHT)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QtWidgets.QWidget(self)
        outer.addWidget(row, 1)
        self.resize_handle = TrackResizeHandle(self)
        outer.addWidget(self.resize_handle)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QtWidgets.QFrame(self)
        self.header.setFixedWidth(190)
        header = QtWidgets.QVBoxLayout(self.header)
        session = editor.controller.session
        assert session is not None
        name = QtWidgets.QPushButton(session.name, self.header)
        name.setFlat(True)
        name.clicked.connect(self.activate)
        name.setToolTip(session.name)
        header.addWidget(name)
        toggles = QtWidgets.QHBoxLayout()
        self.mute = QtWidgets.QPushButton("Mute")
        self.solo = QtWidgets.QPushButton("Solo")
        for button in (self.mute, self.solo):
            button.setCheckable(True)
            button.toggled.connect(self._mix)
            toggles.addWidget(button)
        header.addLayout(toggles)
        header.addWidget(editor.audio_view_toggle)
        self.menu_button = QtWidgets.QToolButton()
        self.menu_button.setText("•••")
        menu = QtWidgets.QMenu(self.menu_button)
        menu.addAction("Recording details", self.details)
        menu.addAction("Create marker at playhead", self.create_marker)
        menu.addAction("Refresh annotation definitions", editor.controller.refresh_libraries)
        menu.addAction("Move track up", lambda: workspace.move_order(track.recording_id, -1))
        menu.addAction("Move track down", lambda: workspace.move_order(track.recording_id, 1))
        menu.addAction("Remove track", lambda: self.remove_requested.emit(track.recording_id))
        self.menu_button.setMenu(menu)
        self.menu_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        header.addWidget(self.menu_button)
        header.addWidget(editor.session_state_value)
        header.addStretch()
        layout.addWidget(self.header)
        timeline = QtWidgets.QWidget(self)
        content = QtWidgets.QVBoxLayout(timeline)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.clip_header = ClipHeader(workspace, track)
        self.clip_header.moved.connect(lambda offset: workspace.place(track.recording_id, offset))
        self.clip_header.activated.connect(self.activate)
        content.addWidget(self.clip_header)
        content.addWidget(editor.waveform, 1)
        layout.addWidget(timeline, 1)
        editor.waveform.workspace_offset = track.offset
        editor.waveform.installEventFilter(self)
        editor.waveform.viewport_changed.connect(self._viewport_requested)
        editor.waveform.selection_changed.connect(self._selection)
        editor.waveform.annotation_selected.connect(self._annotation)
        editor.waveform.seek_requested.connect(self._seek)
        editor.controller.session_changed.connect(self._session_changed)
        workspace.viewport_changed.connect(self.sync_viewport)
        workspace.playhead_changed.connect(self._playhead)
        workspace.selection_changed.connect(self._shared_selection)
        workspace.active_changed.connect(self._active)
        workspace.changed.connect(self.sync)
        editor.new_annotation_button.setText("Cancel / clear selection")
        editor.new_annotation_button.clicked.connect(lambda: workspace.select(None))
        self.sync()

    def activate(self) -> None:
        self.workspace.activate(self.track.recording_id)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if isinstance(event, QtGui.QWheelEvent) and not event.modifiers():
            self.vertical_scroll_requested.emit(event.angleDelta().y())
            event.accept()
            return True
        if event.type() == QtCore.QEvent.Type.MouseButtonPress:
            self.activate()
        return super().eventFilter(watched, event)

    def _mix(self) -> None:
        self.workspace.set_mix(
            self.track.recording_id, muted=self.mute.isChecked(), solo=self.solo.isChecked()
        )

    def _active(self, key: object) -> None:
        active = key == self.track.recording_id
        self.header.setStyleSheet("background: #dbe5ee;" if active else "background: #e5e5e5;")
        if not active:
            self.editor._new_annotation()
        self.inspector_changed.emit()

    def _selection(self, interval: object) -> None:
        if self.workspace.active_id == self.track.recording_id:
            self.workspace.select(interval if isinstance(interval, tuple) else None)
            self.inspector_changed.emit()

    def _annotation(self, value: object) -> None:
        self.workspace.select(None)
        self.inspector_changed.emit()

    def _seek(self, sample: int) -> None:
        self.editor._new_annotation()
        self.workspace.select(None)
        # Workstation connects this signal to its shared player.
        self.inspector_changed.emit()

    def _shared_selection(self, interval: object) -> None:
        view = self.editor.waveform
        if isinstance(interval, tuple):
            view.selection = tuple(
                round((t - self.track.offset) * self.track.sample_rate) for t in interval
            )
        else:
            view.selection = None
        view.update()

    def _session_changed(self, value: object) -> None:
        rows = self.editor.waveform.annotation_rows()[1]
        self.resize_handle.minimum_height = max(220, 162 + rows * 36)
        self.setFixedHeight(max(self.height(), self.resize_handle.minimum_height))
        self.inspector_changed.emit()

    def _viewport_requested(self, start: int, end: int) -> None:
        self.workspace.set_viewport(
            self.track.workspace_time(start), self.track.workspace_time(end)
        )

    def sync_viewport(self, start: float, end: float) -> None:
        view = self.editor.waveform
        with QtCore.QSignalBlocker(view):
            view.set_viewport(
                round((start - self.track.offset) * self.track.sample_rate),
                round((end - self.track.offset) * self.track.sample_rate),
            )
        self.editor._detail_timer.start()
        self.clip_header.update()

    def _playhead(self, seconds: float) -> None:
        self.editor.waveform.set_playhead_seconds(seconds - self.track.offset)

    def sync(self) -> None:
        self.editor.waveform.workspace_offset = self.track.offset
        self.sync_viewport(*self.workspace.viewport)
        self._playhead(self.workspace.playhead)
        self._active(self.workspace.active_id)

    def details(self) -> None:
        editor = self.editor
        QtWidgets.QMessageBox.information(
            self,
            "Recording details",
            "\n".join(
                value.text()
                for value in (
                    editor.name_value,
                    editor.id_value,
                    editor.language_value,
                    editor.duration_value,
                    editor.revision_value,
                )
            ),
        )

    def create_marker(self) -> None:
        self.activate()
        editor = self.editor
        editor._new_annotation()
        editor.concept_search.setText("marker")
        editor.start_sample.setValue(self.track.local_sample(self.workspace.playhead))
        # A point needs no interval; request the form explicitly.
        self.marker_requested.emit()


def make_editor(api: ApplicationClient, runner: TaskSubmitter | None = None) -> TrackEditor:
    editor = TrackEditor(api, task_runner=runner)
    editor.hide()
    editor.pinned_libraries.hide()
    editor.inspector_binding = AnnotationInspector(editor)
    # The compact inspector keeps library pins as secondary concept information.
    for label in editor.editor_scroll.findChildren(QtWidgets.QLabel):
        if label.text() == "Pinned libraries":
            label.hide()
    editor.controller.libraries_changed.connect(lambda _: editor.controller.load_picker_libraries())
    if editor.controller.libraries:
        editor.controller.load_picker_libraries()
    return editor
