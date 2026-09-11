"""Multitrack main window: composition, recording discovery and safe session lifecycle."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from uuid import UUID

from PySide6 import QtCore, QtGui, QtWidgets

from application import RecordingListItem, SyncState
from gui.controller import ApplicationClient, WorkbenchController
from gui.microphone import MicrophoneDialog
from gui.tasks import TaskRunner, TaskSubmitter
from gui.track_stack import TrackStack
from gui.track_widget import TrackEditor, TrackWidget, make_editor
from gui.workspace import Track, WorkspaceController
from gui.workspace_player import WorkspacePlayer


class RecordingsBrowser(QtWidgets.QDialog):
    add_requested = QtCore.Signal(object)

    def __init__(self, catalog: WorkbenchController, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browse existing recordings")
        self.resize(600, 440)
        layout = QtWidgets.QVBoxLayout(self)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search recordings")
        self.items = QtWidgets.QListWidget()
        self.state = QtWidgets.QLabel()
        layout.addWidget(self.search)
        layout.addWidget(self.items)
        layout.addWidget(self.state)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        refresh = buttons.addButton("Refresh", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        add = buttons.addButton("Add recording", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        refresh.clicked.connect(catalog.refresh_recordings)
        add.clicked.connect(self._add)
        buttons.rejected.connect(self.reject)
        self.items.itemDoubleClicked.connect(self._add)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        catalog.recordings_changed.connect(self.populate)
        catalog.recordings_failed.connect(self.state.setText)
        self.populate(catalog.recordings)

    def populate(self, recordings: object) -> None:
        self.items.clear()
        for recording in cast(tuple[RecordingListItem, ...], recordings):
            item = QtWidgets.QListWidgetItem(
                f"{recording.display_name}\n{recording.duration_seconds:.2f} s  ·  "
                f"revision {recording.revision_number}  ·  {recording.audio_status.value}"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, recording)
            self.items.addItem(item)
        self.state.setText(f"{self.items.count()} recordings")
        self._filter()

    def _filter(self) -> None:
        query = self.search.text().casefold()
        for row in range(self.items.count()):
            item = self.items.item(row)
            item.setHidden(query not in item.text().casefold())

    def _add(self) -> None:
        item = self.items.currentItem()
        if item is not None and not item.isHidden():
            self.add_requested.emit(item.data(QtCore.Qt.ItemDataRole.UserRole))
            self.accept()


class WorkstationWindow(QtWidgets.QMainWindow):
    def __init__(self, api: ApplicationClient, *, task_runner: TaskSubmitter | None = None) -> None:
        super().__init__()
        self.api, self.runner = api, task_runner
        self.setWindowTitle("Alignment Workbench")
        self.resize(1400, 850)
        self.workspace = WorkspaceController(self)
        self.player = WorkspacePlayer(self.workspace)
        self.catalog_runner = TaskRunner(self)
        self.catalog = WorkbenchController(api, task_runner or self.catalog_runner, self)
        self.tracks: dict[UUID, TrackWidget] = {}
        self.pending: list[TrackEditor] = []
        self.opening: set[UUID] = set()
        self._shutdown = False
        self._closing = False
        self._retrying = False
        self._removing: UUID | None = None
        self._build_ui()
        self.browser = RecordingsBrowser(self.catalog, self)
        self.browser.add_requested.connect(self.add_recording)
        self.player.error.connect(self._error)
        self.player.state_changed.connect(
            lambda playing: self.play.setText("Pause" if playing else "Play")
        )
        self.workspace.active_changed.connect(self._active)
        self.workspace.changed.connect(self._changed)
        self.workspace.playhead_changed.connect(self._time)
        self.workspace.viewport_changed.connect(self._scroll_position)
        self.catalog.recordings_failed.connect(self._error)
        self.catalog.recovery_error.connect(self._error)
        self.catalog.refresh_all()

    def _build_ui(self) -> None:
        toolbar = self.addToolBar("Workspace")
        toolbar.setMovable(False)
        self.add_button = QtWidgets.QToolButton()
        self.add_button.setText("+ Add recording")
        self.add_button.setStyleSheet("padding: 6px; font-weight: bold;")
        menu = QtWidgets.QMenu(self)
        menu.addAction("Record new audio", self._record)
        menu.addSeparator()
        menu.addAction("Import audio file", self.import_audio)
        menu.addSeparator()
        menu.addAction("Browse existing recordings", self._browse)
        self.add_button.setMenu(menu)
        self.add_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        toolbar.addWidget(self.add_button)
        self.play = QtWidgets.QPushButton("Play")
        self.play.clicked.connect(
            lambda: self.player.pause() if self.player.playing else self.player.play()
        )
        toolbar.addWidget(self.play)
        toolbar.addAction("Stop", self.player.stop)
        self.time = QtWidgets.QLabel("0.00 / 0.00 s")
        self.time.setMinimumWidth(130)
        toolbar.addWidget(self.time)
        self.undo = toolbar.addAction("Undo", lambda: self._action("undo"))
        self.undo.setShortcut(QtGui.QKeySequence.StandardKey.Undo)
        self.redo = toolbar.addAction("Redo", lambda: self._action("redo"))
        self.redo.setShortcut(QtGui.QKeySequence.StandardKey.Redo)
        self.save = toolbar.addAction("Save revision", self._save)
        self.save.setShortcut(QtGui.QKeySequence.StandardKey.Save)
        toolbar.addAction("Retry pending saves", self.retry)
        toolbar.addSeparator()
        toolbar.addAction("Fit workspace", self.workspace.fit)
        toolbar.addAction("Zoom to selection", self._zoom_selection)
        toolbar.addAction("Zoom in", lambda: self.workspace.zoom(0.5))
        toolbar.addAction("Zoom out", lambda: self.workspace.zoom(2))
        toolbar.addAction("Clear selection", self.clear_selection)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        self.error_panel = QtWidgets.QWidget()
        error_layout = QtWidgets.QHBoxLayout(self.error_panel)
        self.error_label = QtWidgets.QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b32635;")
        error_layout.addWidget(self.error_label, 1)
        retry = QtWidgets.QPushButton("Retry pending saves")
        retry.clicked.connect(self.retry)
        error_layout.addWidget(retry)
        dismiss = QtWidgets.QPushButton("Dismiss")
        dismiss.clicked.connect(self.error_panel.hide)
        error_layout.addWidget(dismiss)
        self.error_panel.hide()
        layout.addWidget(self.error_panel)
        split = QtWidgets.QSplitter()
        self.track_scroll = QtWidgets.QScrollArea()
        self.track_scroll.setWidgetResizable(True)
        self.track_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_stack = TrackStack()
        self.track_scroll.setWidget(self.track_stack)
        split.addWidget(self.track_scroll)
        self.inspector = QtWidgets.QStackedWidget()
        self.inspector.setMinimumWidth(300)
        self.empty = QtWidgets.QLabel(
            "Select an annotation or drag over audio\nto select an interval."
        )
        self.empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.inspector.addWidget(self.empty)
        split.addWidget(self.inspector)
        split.setSizes([1050, 350])
        layout.addWidget(split, 1)
        self.horizontal = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
        self.horizontal.valueChanged.connect(self._pan)
        layout.addWidget(self.horizontal)
        self.setCentralWidget(central)
        self._active(None)

    def _record(self) -> None:
        self.player.pause()
        # Keep the take alive until the asynchronous import has copied it, or
        # the user cancels. Failed imports leave the dialog and take available.
        with TemporaryDirectory(prefix="workbench-microphone-") as directory:
            dialog = MicrophoneDialog(Path(directory) / "recording.wav", self)
            editor: TrackEditor | None = None

            def add_take() -> None:
                nonlocal editor
                if editor is None:
                    editor = self._new_editor()
                    editor.controller.error.connect(dialog.status.setText)
                    editor.controller.busy_changed.connect(dialog.set_saving)
                    editor.controller.session_changed.connect(import_finished)
                editor.controller.import_recording(
                    dialog.path, name=dialog.name.text().strip(),
                    language=dialog.language.text().strip(),
                )

            def import_finished(value: object) -> None:
                if value is not None:
                    dialog.accept()

            dialog.add_requested.connect(add_take)
            try:
                dialog.exec()
            finally:
                dialog._release()
                if editor is not None:
                    editor.controller.error.disconnect(dialog.status.setText)
                    editor.controller.busy_changed.disconnect(dialog.set_saving)
                    editor.controller.session_changed.disconnect(import_finished)
                    if editor in self.pending:
                        self.pending.remove(editor)
                        editor.shutdown()
                        editor.deleteLater()
                dialog.deleteLater()

    def _browse(self) -> None:
        self.catalog.refresh_recordings()
        self.browser.show()
        self.browser.raise_()

    def _new_editor(self) -> TrackEditor:
        editor = make_editor(self.api, self.runner)
        self.pending.append(editor)
        editor.controller.session_changed.connect(lambda value: self._install(editor))
        editor.controller.error.connect(self._error)
        editor.controller.recovery_error.connect(self._error)
        editor.controller.notice.connect(
            lambda message: self.statusBar().showMessage(message, 5000)
        )
        editor.controller.busy_changed.connect(lambda _: self._busy_finished())
        editor.controller.save_finished.connect(self._save_finished)
        editor.controller.recovery_finished.connect(self._save_finished)
        return editor

    def add_recording(self, item: RecordingListItem) -> None:
        if item.recording_id in self.tracks:
            self.workspace.activate(item.recording_id)
            return
        if item.recording_id in self.opening:
            return
        self.opening.add(item.recording_id)
        editor = self._new_editor()
        editor.controller.busy_changed.connect(
            lambda busy: self.opening.discard(item.recording_id) if not busy else None
        )
        editor.controller.open_recording(item)

    def import_audio(self) -> None:
        editor = self._new_editor()
        editor._import_recording()
        if not editor.controller.busy and editor.controller.session is None:
            self.pending.remove(editor)
            editor.shutdown()
            editor.deleteLater()

    def _install(self, editor: TrackEditor) -> None:
        session = editor.controller.session
        if session is None or session.recording_id in self.tracks:
            self._active(self.workspace.active_id)
            return
        asset = session.audio.asset
        track = Track(
            session.recording_id,
            session.audio.local_path,
            asset.duration_seconds,
            asset.sample_rate_hz,
        )
        widget = TrackWidget(editor, track, self.workspace)
        self.tracks[track.recording_id] = widget
        if editor in self.pending:
            self.pending.remove(editor)
        self.track_stack.addWidget(widget)
        self.inspector.addWidget(editor.editor_scroll)
        widget.remove_requested.connect(self.remove_track)
        widget.vertical_scroll_requested.connect(self._vertical_scroll)
        widget.inspector_changed.connect(lambda: self._active(self.workspace.active_id))
        widget.marker_requested.connect(
            lambda: self.inspector.setCurrentWidget(editor.editor_scroll)
        )
        editor.waveform.seek_requested.connect(
            lambda sample: self.player.seek(track.workspace_time(sample))
        )
        self.workspace.add(track)
        self._active(track.recording_id)

    def _active(self, key: object) -> None:
        widget = self.tracks.get(key) if isinstance(key, UUID) else None
        if widget:
            editor = widget.editor
            selected = (
                editor._selected_annotation_id is not None or self.workspace.selection is not None
            )
            self.inspector.setCurrentWidget(editor.editor_scroll if selected else self.empty)
            self.undo.setEnabled(editor.undo_action.isEnabled())
            self.redo.setEnabled(editor.redo_action.isEnabled())
            self.save.setEnabled(editor.save_action.isEnabled())
        else:
            self.inspector.setCurrentWidget(self.empty)
            for action in (self.undo, self.redo, self.save):
                action.setEnabled(False)
        if self._retrying or self._closing:
            for action in (self.undo, self.redo, self.save):
                action.setEnabled(False)
        for track_widget in self.tracks.values():
            locked = self._retrying or self._closing
            track_widget.editor.editor_scroll.setEnabled(not locked)
            track_widget.editor.waveform.set_editable(
                not locked and not track_widget.editor.controller.busy
            )
        self.add_button.setEnabled(not self._closing and not self._retrying)

    def _changed(self) -> None:
        for index, track in enumerate(self.workspace.tracks):
            self.track_stack.insertWidget(index, self.tracks[track.recording_id])
        self._time(self.workspace.playhead)
        self._scroll_position(*self.workspace.viewport)

    def _time(self, seconds: float) -> None:
        self.time.setText(f"{seconds:.2f} / {self.workspace.duration:.2f} s")

    def _vertical_scroll(self, delta: int) -> None:
        bar = self.track_scroll.verticalScrollBar()
        bar.setValue(bar.value() - delta)

    def _scroll_position(self, start: float, end: float) -> None:
        with QtCore.QSignalBlocker(self.horizontal):
            self.horizontal.setRange(0, round(max(self.workspace.duration, end) * 1000))
            self.horizontal.setPageStep(round((end - start) * 1000))
            self.horizontal.setValue(round(start * 1000))

    def _pan(self, value: int) -> None:
        start, end = self.workspace.viewport
        self.workspace.set_viewport(value / 1000, value / 1000 + end - start)

    def _zoom_selection(self) -> None:
        if self.workspace.selection_times:
            self.workspace.set_viewport(*self.workspace.selection_times)

    def clear_selection(self) -> None:
        for widget in self.tracks.values():
            widget.editor._new_annotation()
        self.workspace.select(None)
        self._active(self.workspace.active_id)

    def _action(self, name: str) -> None:
        widget = self.tracks.get(self.workspace.active_id) if self.workspace.active_id else None
        if widget:
            getattr(widget.editor.controller, name)()

    def _save(self) -> None:
        widget = self.tracks.get(self.workspace.active_id) if self.workspace.active_id else None
        if widget:
            widget.editor._save()

    def _error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_panel.show()

    def retry(self) -> None:
        if self._retrying:
            return
        editors = [w.editor for w in self.tracks.values()]
        if any(
            e.controller.busy or (e.controller.session and e.controller.session.dirty)
            for e in editors
        ):
            self._error(
                "Save current changes and wait for operations before retrying pending saves."
            )
            return
        # Each controller reloads its own clean session; sequence global recovery calls.
        self._retry_queue = editors or [self._new_editor()]
        self._retrying = True
        self._active(self.workspace.active_id)
        self._retry_next()

    def _retry_next(self) -> None:
        if not self._retry_queue:
            self._retrying = False
            self._active(self.workspace.active_id)
            self.error_panel.hide()
            self.catalog.refresh_recordings()
            return
        editor = self._retry_queue.pop(0)

        def done(ok: bool) -> None:
            editor.controller.recovery_finished.disconnect(done)
            if ok:
                QtCore.QTimer.singleShot(0, self._retry_next)
            else:
                self._retrying = False
                self._active(self.workspace.active_id)

        editor.controller.recovery_finished.connect(done)
        editor.controller.retry_recovery()

    def remove_track(self, key: UUID) -> None:
        self._removing = key
        self._continue_close()

    def _save_finished(self, ok: bool) -> None:
        if not ok:
            self._closing = False
            self._removing = None
            self._active(self.workspace.active_id)
            self._error(
                "Save did not complete in the database. Resolve the error or retry pending saves."
            )

    def _busy_finished(self) -> None:
        self._active(self.workspace.active_id)
        if self._closing or self._removing is not None:
            QtCore.QTimer.singleShot(0, self._continue_close)

    def _continue_close(self) -> None:
        if not self._closing and self._removing is None:
            return
        widgets = (
            list(self.tracks.values())
            if self._closing
            else [self.tracks[cast(UUID, self._removing)]]
        )
        if any(w.editor.controller.busy for w in widgets) or any(
            e.controller.busy for e in self.pending
        ):
            return
        for widget in widgets:
            editor = widget.editor
            session = editor.controller.session
            if session is None:
                continue
            if session.sync_state is SyncState.CONFLICT:
                self._save_finished(False)
                return
            if session.dirty:
                self.workspace.activate(session.recording_id)
                editor.save_dialog.setWindowTitle(f"Save before closing: {session.name}")
                if editor.save_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    self._closing = False
                    self._removing = None
                    self._active(self.workspace.active_id)
                    return
                editor.controller.save(
                    author=editor.author_edit.text().strip() or None,
                    message=editor.save_message_edit.text().strip() or None,
                )
                return
            if session.sync_state is SyncState.PENDING:
                editor.controller.retry_recovery()
                return
        if self._closing:
            self.shutdown()
            self.close()
        elif self._removing is not None:
            key, self._removing = self._removing, None
            widget = self.tracks.pop(key)
            self.player.pause()
            self.workspace.remove(key)
            self.inspector.removeWidget(widget.editor.editor_scroll)
            widget.editor.shutdown()
            widget.editor.editor_scroll.deleteLater()
            widget.editor.deleteLater()
            widget.setParent(None)
            widget.deleteLater()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._shutdown:
            event.accept()
            return
        event.ignore()
        self._closing = True
        self.player.pause()
        self._continue_close()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.player.stop()
        for editor in [w.editor for w in self.tracks.values()] + self.pending:
            editor.shutdown()
        self.catalog.shutdown()
        self.catalog_runner.shutdown()
