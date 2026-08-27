from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6 import QtCore, QtGui, QtWidgets
from spectrogram_playground.audio.decoding import load_track
from spectrogram_playground.model.track import Track as SpectrogramTrack

from alignment_workbench.audio.engine import SessionAudioEngine
from alignment_workbench.audio.rendering import write_snapshot_to_wav
from alignment_workbench.services.models import IngestRequest, RecordingDetail
from alignment_workbench.services.registry import RegistryServices
from alignment_workbench.services.tasks import DuplicateTaskError, TaskManager
from alignment_workbench.state.editor import (
    EditorSession,
    Segment,
    SessionEvent,
    SessionEventType,
)
from alignment_workbench.ui.ab_comparison import ABComparisonController, corresponding_segment
from alignment_workbench.ui.library_panel import LibraryPanel
from alignment_workbench.ui.recording_panel import AudioFileOwnership, RecordingPanel
from alignment_workbench.ui.sound_inspector import SoundInspector
from alignment_workbench.ui.spectrum_dock import SpectrumPanel
from alignment_workbench.ui.timeline import TimelineEditor
from alignment_workbench.ui.transport import TransportToolbar


@dataclass(frozen=True, slots=True)
class LoadedPayload:
    detail: RecordingDetail | None
    audio: SpectrogramTrack
    origin: str


@dataclass(frozen=True, slots=True)
class InsertPayload:
    track_id: UUID
    audio: SpectrogramTrack


@dataclass(frozen=True, slots=True)
class IngestPayload:
    request: IngestRequest
    result: dict[str, object]


@dataclass(frozen=True, slots=True)
class RenderPayload:
    path: Path
    metadata: dict[str, str | None]


class MainWindow(QtWidgets.QMainWindow):
    SHUTDOWN_WAIT_MS = 1_500

    def __init__(
        self,
        services: RegistryServices | None = None,
        startup_error: str | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Alignment Workbench")
        self.resize(1500, 920)
        self.session = EditorSession()
        self.services = services
        self.startup_error = startup_error
        self._shutdown_started = False
        self.tasks = TaskManager(self)
        self.audio = SessionAudioEngine(self.session)
        self.ab_comparison = ABComparisonController(self.session, self.audio)
        self._build_ui()
        self._build_actions()
        self.session.subscribe(self._state_changed)
        self.tasks.completed.connect(self._task_completed)
        self.tasks.failed.connect(self._task_failed)
        QtWidgets.QApplication.instance().focusChanged.connect(self._focus_changed)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.audition_stop_timer = QtCore.QTimer(self)
        self.audition_stop_timer.setSingleShot(True)
        self.audition_stop_timer.timeout.connect(self.audio.engine.pause)
        self._check_connection()

    def _build_ui(self) -> None:
        self.transport = TransportToolbar(self.session, self)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self.transport)
        self.transport.play_pause.connect(self.audio.play_pause)
        self.transport.stop.connect(self._stop)
        self.transport.record.connect(self._show_recording)
        self.transport.loop_changed.connect(self.audio.set_loop)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.library = LibraryPanel(self.services, self.tasks, auto_search=False)
        self.library.setMinimumWidth(270)
        self.library.setMaximumWidth(430)
        self.library.add_recording.connect(self.load_recording)
        self.library.relink_recording.connect(self.relink_recording)
        self.library.error.connect(self._show_status_error)
        splitter.addWidget(self.library)
        self.timeline = TimelineEditor(self.session)
        self.timeline.analysis_failed.connect(self._show_status_error)
        splitter.addWidget(self.timeline)
        self.inspector = SoundInspector(self.session, self.services, self.tasks)
        self.inspector.setMinimumWidth(260)
        self.inspector.setMaximumWidth(380)
        self.inspector.error.connect(self._show_status_error)
        self.inspector.audition_requested.connect(self._audition)
        self.inspector.compare_requested.connect(self._compare)
        splitter.addWidget(self.inspector)
        splitter.setSizes((310, 900, 310))
        self.setCentralWidget(splitter)

        self.recording = RecordingPanel(self.tasks)
        self.recording.preview_audio.connect(self.load_local_audio)
        self.recording.save_requested.connect(self._ingest)
        self.recording.create_speaker_requested.connect(self._create_speaker)
        self.recording.error.connect(self._show_status_error)
        self.recording_dock = QtWidgets.QDockWidget("Record / Import", self)
        self.recording_dock.setObjectName("recordingDock")
        self.recording_dock.setWidget(self.recording)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, self.recording_dock)

        self.spectrum = SpectrumPanel(self.session, self.tasks)
        spectrum_dock = QtWidgets.QDockWidget("Spectrum", self)
        spectrum_dock.setObjectName("spectrumDock")
        spectrum_dock.setWidget(self.spectrum)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, spectrum_dock)
        spectrum_dock.hide()
        self.spectrum_dock = spectrum_dock
        self.statusBar().showMessage("Ready")
        if self.startup_error:
            self.transport.connection.setToolTip(self.startup_error)
            self.statusBar().showMessage(self.startup_error, 15_000)

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._menu_action(file_menu, "Import audioâ€¦", self.recording.import_audio, "Ctrl+O")
        self._menu_action(
            file_menu,
            "Import into active trackâ€¦",
            self._import_into_active,
            "Ctrl+Shift+O",
        )
        self._menu_action(file_menu, "Save as new recordingâ€¦", self._commit_track)
        file_menu.addSeparator()
        self._menu_action(file_menu, "Quit", self.close, "Ctrl+Q")

        edit = self.menuBar().addMenu("&Edit")
        self.undo_action = self._menu_action(edit, "Undo", self.session.commands.undo, "Ctrl+Z")
        self.redo_action = self._menu_action(
            edit, "Redo", self.session.commands.redo, "Ctrl+Shift+Z"
        )
        edit.addSeparator()
        cut_action = self._menu_action(
            edit, "Cut", lambda: self._edit(self.session.cut_selection), "Ctrl+X"
        )
        copy_action = self._menu_action(
            edit, "Copy", lambda: self._edit(self.session.copy_selection), "Ctrl+C"
        )
        paste_action = self._menu_action(
            edit, "Paste", lambda: self._edit(self.session.paste), "Ctrl+V"
        )
        delete_action = self._menu_action(
            edit, "Delete", lambda: self._edit(self.session.delete_selection), "Delete"
        )
        self._menu_action(edit, "Split at playhead", lambda: self._edit(self.session.split_at))
        self._menu_action(edit, "Move clip left 100 ms", lambda: self._move_clip(-1))
        self._menu_action(edit, "Move clip right 100 ms", lambda: self._move_clip(1))
        self.editor_shortcut_actions = (
            self.undo_action,
            self.redo_action,
            cut_action,
            copy_action,
            paste_action,
            delete_action,
        )

        view = self.menuBar().addMenu("&View")
        self._menu_action(view, "Zoom in", lambda: self._zoom(0.7), "+")
        self._menu_action(view, "Zoom out", lambda: self._zoom(1.4), "-")
        self._menu_action(view, "Zoom to selection", self._zoom_selection)
        self._menu_action(view, "Zoom to full project", self._zoom_full)
        self._menu_action(view, "Spectrum panel", self._toggle_spectrum)

        registry = self.menuBar().addMenu("&Registry")
        self._menu_action(registry, "Retry connection", self._check_connection)
        self._menu_action(registry, "Refresh catalog", self.library.search)

    def _menu_action(
        self,
        menu: QtWidgets.QMenu,
        label: str,
        callback: object,
        shortcut: str | None = None,
    ) -> QtGui.QAction:
        action = menu.addAction(label)
        if shortcut:
            action.setShortcut(QtGui.QKeySequence(shortcut))
        action.triggered.connect(callback)  # type: ignore[arg-type]
        return action

    def _state_changed(self, event: SessionEvent) -> None:
        if event.reason is SessionEventType.HISTORY:
            self._focus_changed(None, QtWidgets.QApplication.focusWidget())
        if event.reason in {
            SessionEventType.PLAYHEAD,
            SessionEventType.TRACK_CONTENT,
            SessionEventType.TRACK_ADDED,
            SessionEventType.TRACK_REMOVED,
        } or (
            event.reason is SessionEventType.BATCH
            and bool(
                event.changes
                & {
                    SessionEventType.PLAYHEAD,
                    SessionEventType.TRACK_CONTENT,
                    SessionEventType.TRACK_REMOVED,
                }
            )
        ):
            self.transport.update_time(self.session.playhead_frame)

    def _focus_changed(
        self, _old: QtWidgets.QWidget | None, current: QtWidgets.QWidget | None
    ) -> None:
        text_active = self._is_text_input(current)
        if hasattr(self, "editor_shortcut_actions"):
            for action in self.editor_shortcut_actions[2:]:
                action.setEnabled(not text_active)
            self.undo_action.setEnabled(not text_active and self.session.commands.can_undo)
            self.redo_action.setEnabled(not text_active and self.session.commands.can_redo)
        self.transport.set_shortcuts_enabled(not text_active)

    def _check_connection(self) -> None:
        if self.services is None:
            self.transport.connection.setText("DB: disconnected")
            return
        self.transport.connection.setText("DB: checkingâ€¦")
        self.tasks.submit(
            "connection",
            lambda _cancel, _progress: self.services.check_connection(),
            replace=True,
        )

    def _load_speakers(self) -> None:
        if self.services is None:
            return
        self.tasks.submit(
            "speakers",
            lambda _cancel, _progress: self.services.speakers(),
            replace=True,
        )

    def _create_speaker(self, speaker_id: str, display_name: str, language: str) -> None:
        if self.services is None:
            self._show_status_error("Registry service is unavailable")
            return
        try:
            self.tasks.submit(
                "speaker-create",
                lambda _cancel, _progress: self.services.create_speaker(
                    speaker_id, display_name, language
                ),
                mutation=True,
            )
        except DuplicateTaskError as exc:
            self._show_status_error(str(exc))

    @QtCore.Slot(str)
    def load_recording(self, recording_id: str) -> None:
        if self.services is None:
            self._show_status_error("Registry service is unavailable")
            return
        self.statusBar().showMessage(f"Loading {recording_id}â€¦")

        def operation(cancel: object, progress: object) -> LoadedPayload:
            detail = self.services.recording(recording_id, fetch_missing=True)
            if cancel.is_set():  # type: ignore[attr-defined]
                raise RuntimeError("recording load was cancelled")
            if detail.audio_path is None:
                raise RuntimeError(
                    "Database metadata exists, but audio is unavailable locally and could not "
                    "be fetched"
                )
            audio = load_track(
                detail.audio_path,
                project_rate=self.session.sample_rate,
                color_index=len(self.session.tracks),
            )
            if cancel.is_set():  # type: ignore[attr-defined]
                raise RuntimeError("recording load was cancelled")
            return LoadedPayload(detail, audio, "registry")

        self.tasks.submit(f"load:{recording_id}", operation, replace=True)

    @QtCore.Slot(object, str)
    def load_local_audio(self, path: Path, origin: str) -> None:
        source = Path(path)
        self.statusBar().showMessage(f"Decoding {source.name}â€¦")
        self.tasks.submit(
            f"local:{source}",
            lambda _cancel, _progress: LoadedPayload(
                None,
                load_track(
                    source,
                    project_rate=self.session.sample_rate,
                    color_index=len(self.session.tracks),
                ),
                origin,
            ),
            replace=True,
        )

    def _import_into_active(self) -> None:
        track = self.session.active_track
        if track is None:
            self._show_status_error("Add or load a track before importing into it")
            return
        value, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import audio into active track",
            "",
            "Audio (*.wav *.flac *.ogg *.mp3);;All files (*)",
        )
        if not value:
            return
        source = Path(value)
        track_id = track.id
        self.statusBar().showMessage(f"Decoding {source.name}â€¦")
        self.tasks.submit(
            f"insert-local:{track_id}",
            lambda _cancel, _progress: InsertPayload(
                track_id,
                load_track(
                    source,
                    project_rate=self.session.sample_rate,
                    color_index=len(self.session.tracks),
                ),
            ),
            replace=True,
        )

    @QtCore.Slot(str)
    def relink_recording(self, recording_id: str) -> None:
        if self.services is None:
            self._show_status_error("Registry service is unavailable")
            return
        value, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            f"Locate audio for {recording_id}",
            "",
            "Audio (*.wav *.flac *.ogg *.mp3);;All files (*)",
        )
        if not value:
            return
        source = Path(value)
        self.tasks.submit(
            f"relink:{recording_id}",
            lambda _cancel, _progress: LoadedPayload(
                self.services.recording(recording_id, fetch_missing=False),
                load_track(
                    source,
                    project_rate=self.session.sample_rate,
                    color_index=len(self.session.tracks),
                ),
                "relinked-local-audio",
            ),
            replace=True,
        )

    def _add_payload(self, payload: LoadedPayload) -> None:
        audio = payload.audio
        detail = payload.detail
        summary = detail.summary if detail else None
        words = self._segments(detail.words) if detail else []
        phones = self._segments(detail.phones) if detail else []
        self.session.add_audio_track(
            audio.playback_samples,
            name=summary.recording_id if summary else audio.name,
            identity=(
                f"recording-version:{summary.recording_version_id}"
                if summary
                else f"local:{audio.source_path}"
            ),
            path=audio.source_path,
            original_rate=audio.original_rate,
            channels=audio.channels,
            recording_id=summary.recording_id if summary else None,
            recording_version_id=summary.recording_version_id if summary else None,
            speaker_id=summary.speaker_id if summary else self.recording.selected_speaker_id(),
            transcript=summary.transcript if summary else self.recording.transcript.text(),
            language=summary.language if summary else self.recording.language.currentText(),
            words=words,
            phones=phones,
        )
        self.statusBar().showMessage(f"Added {audio.name}", 4_000)

    def _segments(self, source: tuple[object, ...]) -> list[Segment]:
        output: list[Segment] = []
        for item in source:
            rate = item.timebase_sample_rate_hz  # type: ignore[attr-defined]
            output.append(
                Segment(
                    id=item.id,  # type: ignore[attr-defined]
                    kind=item.kind,  # type: ignore[attr-defined]
                    label=item.label,  # type: ignore[attr-defined]
                    start_frame=round(item.start_sample * self.session.sample_rate / rate),  # type: ignore[attr-defined]
                    end_frame=round(item.end_sample * self.session.sample_rate / rate),  # type: ignore[attr-defined]
                    timebase_sample_rate_hz=rate,
                    parent_id=item.parent_id,  # type: ignore[attr-defined]
                    confidence=item.confidence,  # type: ignore[attr-defined]
                    review_state=item.review_state,  # type: ignore[attr-defined]
                    model_label=item.model_label,  # type: ignore[attr-defined]
                    model_start_frame=(
                        round(item.model_start_sample * self.session.sample_rate / rate)  # type: ignore[attr-defined]
                        if item.model_start_sample is not None  # type: ignore[attr-defined]
                        else None
                    ),
                    model_end_frame=(
                        round(item.model_end_sample * self.session.sample_rate / rate)  # type: ignore[attr-defined]
                        if item.model_end_sample is not None  # type: ignore[attr-defined]
                        else None
                    ),
                    provenance=item.provenance,  # type: ignore[attr-defined]
                    effective_revision_id=item.effective_revision_id,  # type: ignore[attr-defined]
                    model_segment_id=item.model_segment_id or item.id,  # type: ignore[attr-defined]
                    topology_version=item.topology_version,  # type: ignore[attr-defined]
                    saved_label=item.label,  # type: ignore[attr-defined]
                    saved_start_frame=round(
                        item.start_sample * self.session.sample_rate / rate  # type: ignore[attr-defined]
                    ),
                    saved_end_frame=round(
                        item.end_sample * self.session.sample_rate / rate  # type: ignore[attr-defined]
                    ),
                )
            )
        return output

    def _ingest(self, request: IngestRequest) -> None:
        if self.services is None:
            self._show_status_error("Registry service is unavailable")
            return
        self.recording.status.setText("Preparing, uploading, and aligningâ€¦")
        try:
            self.tasks.submit(
                "ingest",
                lambda _cancel, _progress: IngestPayload(
                    request, self.services.ingest(request, align=True)
                ),
                mutation=True,
            )
        except DuplicateTaskError as exc:
            self._show_status_error(str(exc))

    def _commit_track(self) -> None:
        track = self.session.active_track
        if track is None:
            self._show_status_error("No active track")
            return
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Render edited track", f"{track.name}-edited.wav", "WAV (*.wav)"
        )
        if not path:
            return
        metadata = {
            "recording_id": f"{track.recording_id or track.name}-edited",
            "transcript": track.transcript,
            "speaker_id": track.speaker_id,
            "language": track.language,
        }
        render_plan = self.session.capture_render_plan(track.id)
        self.statusBar().showMessage("Rendering edited trackâ€¦")
        try:
            self.tasks.submit(
                "render",
                lambda cancel, _progress: RenderPayload(
                    write_snapshot_to_wav(
                        EditorSession.compose_render_plan(render_plan).samples,
                        self.session.sample_rate,
                        Path(path),
                        cancel,
                    ),
                    metadata,
                ),
                mutation=True,
            )
        except DuplicateTaskError as exc:
            self._show_status_error(str(exc))

    def _audition(self, loop: bool) -> None:
        if not self.session.selection.active:
            return
        self.audio.set_loop(loop)
        assert self.session.selection.start is not None
        self.audio.seek(self.session.selection.start)
        self.audio.engine.play()
        if loop:
            self.audition_stop_timer.stop()
        else:
            duration_ms = round(
                self.session.selection.duration_frames * 1000 / self.session.sample_rate
            )
            self.audition_stop_timer.start(max(1, duration_ms))

    def _stop(self) -> None:
        self.audition_stop_timer.stop()
        self.ab_comparison.cancel()
        self.audio.stop()

    def _compare(self) -> None:
        reference_id = self.session.reference_track_id
        active = self.session.active_track
        if reference_id is None or active is None or reference_id == active.id:
            self._show_status_error("Select a practice track and a different reference track")
            return
        reference = self.session.track(reference_id)
        selection = self.session.selected_segment
        if selection is None:
            self._show_status_error("Select a word or sound segment for A/B playback")
            return
        segment = self.session.segment(*selection)
        reference_segment = corresponding_segment(active, reference, segment)
        if reference_segment is None:
            self._show_status_error(
                "No unambiguous reference correspondence; check token/parent alignment metadata"
            )
            return
        self.ab_comparison.start(active.id, segment, reference.id, reference_segment)

    def _edit(self, operation: object) -> None:
        if self._text_input_active():
            return
        try:
            operation()  # type: ignore[operator]
        except ValueError as exc:
            self._show_status_error(str(exc))

    def _move_clip(self, direction: int) -> None:
        track = self.session.active_track
        if track is None:
            return
        clip = next(
            (
                item
                for item in track.clips
                if item.timeline_start_frame
                <= self.session.playhead_frame
                < item.timeline_end_frame
            ),
            None,
        )
        if clip is None:
            self._show_status_error("Place the playhead inside the clip to move")
            return
        self._edit(
            lambda: self.session.move_clip(
                clip.id, direction * max(1, self.session.sample_rate // 10)
            )
        )

    def _zoom(self, factor: float) -> None:
        self.session.zoom_viewport(factor, self.session.playhead_frame)

    def _zoom_selection(self) -> None:
        if self.session.selection.active:
            assert (
                self.session.selection.start is not None and self.session.selection.end is not None
            )
            self.session.set_viewport(
                self.session.selection.start,
                self.session.selection.end,
            )

    def _zoom_full(self) -> None:
        self.session.fit_viewport()

    def _toggle_spectrum(self) -> None:
        self.spectrum_dock.setVisible(not self.spectrum_dock.isVisible())

    def _show_recording(self) -> None:
        self.recording_dock.show()
        self.recording.start()

    @QtCore.Slot(str, object, object)
    def _task_completed(self, category: str, _request_id: object, result: object) -> None:
        if category == "connection":
            usable = bool(result.get("usable"))  # type: ignore[union-attr]
            self.transport.connection.setText("DB: connected" if usable else "DB: unavailable")
            mfa = result.get("mfa", {})  # type: ignore[union-attr]
            self.transport.connection.setToolTip(str(mfa.get("message", "")))
            if usable:
                self.library.search()
                self._load_speakers()
        elif category.startswith(("load:", "local:", "relink:")) and isinstance(
            result, LoadedPayload
        ):
            self._add_payload(result)
        elif category.startswith("insert-local:") and isinstance(result, InsertPayload):
            audio = result.audio
            try:
                self.session.add_audio_clip(
                    result.track_id,
                    audio.playback_samples,
                    identity=f"local:{audio.source_path}",
                    path=audio.source_path,
                    original_rate=audio.original_rate,
                    channels=audio.channels,
                )
            except StopIteration:
                self._show_status_error("The target track was removed before import completed")
            else:
                self.statusBar().showMessage(
                    f"Added {audio.name} to the active track at the playhead", 4_000
                )
        elif category == "ingest":
            assert isinstance(result, IngestPayload)
            item = (result.result.get("items") or [{}])[0]  # type: ignore[union-attr]
            outcome = item.get("outcome")
            if outcome == "skipped":
                self._resolve_existing_recording(result.request)
            else:
                recording_id = item.get("recording_id")
                self.recording.status.setText(f"Saved and aligned Â· {outcome}")
                if recording_id:
                    self.load_recording(str(recording_id))
        elif category == "speakers":
            self.recording.set_speakers(result)  # type: ignore[arg-type]
        elif category == "speaker-create":
            self._load_speakers()
            self.recording.speaker.setCurrentText(str(result["id"]))  # type: ignore[index]
        elif category == "render":
            assert isinstance(result, RenderPayload)
            metadata = result.metadata
            self.recording.set_artifact(result.path, AudioFileOwnership.USER_EXPORTED)
            self.recording.recording_id.setText(str(metadata.get("recording_id") or ""))
            self.recording.transcript.setText(str(metadata.get("transcript") or ""))
            self.recording.speaker.setCurrentText(str(metadata.get("speaker_id") or ""))
            self.recording.language.setCurrentText(str(metadata.get("language") or "it"))
            self.recording.status.setText(
                "Rendered a new artifact; review metadata and Save + align"
            )
            self.recording_dock.show()

    @QtCore.Slot(str, object, str)
    def _task_failed(self, category: str, _request_id: object, message: str) -> None:
        if category == "connection":
            self.transport.connection.setText("DB: disconnected")
        if category == "ingest":
            self.recording.status.setText(message)
        if category.startswith(
            ("load:", "local:", "relink:", "insert-local:", "ingest", "connection", "render")
        ):
            self._show_status_error(message)

    def _resolve_existing_recording(self, request: IngestRequest) -> None:
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle("Recording already exists")
        dialog.setText(f"{request.recording_id} already exists.")
        cancel = dialog.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        load = dialog.addButton("Load existing", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        version = dialog.addButton(
            "Create new version", QtWidgets.QMessageBox.ButtonRole.DestructiveRole
        )
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is load:
            self.load_recording(request.recording_id)
        elif clicked is version:
            self._ingest(
                IngestRequest(
                    recording_id=request.recording_id,
                    audio_path=request.audio_path,
                    transcript=request.transcript,
                    language=request.language,
                    speaker_id=request.speaker_id,
                    metadata=request.metadata,
                    overwrite_existing=True,
                )
            )
        elif clicked is cancel:
            self.recording.status.setText("Save cancelled")

    def _tick(self) -> None:
        if self.audio.is_playing:
            self.session.set_playhead(self.audio.current_frame)

    def _text_input_active(self) -> bool:
        return self._is_text_input(QtWidgets.QApplication.focusWidget())

    @staticmethod
    def _is_text_input(focus: QtWidgets.QWidget | None) -> bool:
        return isinstance(
            focus,
            (
                QtWidgets.QLineEdit,
                QtWidgets.QTextEdit,
                QtWidgets.QPlainTextEdit,
                QtWidgets.QSpinBox,
                QtWidgets.QComboBox,
            ),
        )

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if self._text_input_active():
            return super().keyPressEvent(event)
        key = event.key()
        shift = bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
        if key in {QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right}:
            direction = -1 if key == QtCore.Qt.Key.Key_Left else 1
            step = max(1, self.session.sample_rate // 100)
            target = min(
                max(0, self.session.playhead_frame + direction * step),
                self.session.total_frames,
            )
            if shift:
                anchor = (
                    self.session.selection.start
                    if self.session.selection.active
                    else self.session.playhead_frame
                )
                self.session.set_selection(anchor or 0, target)
            self.session.set_playhead(target)
            event.accept()
            return
        if key == QtCore.Qt.Key.Key_Escape:
            selection = self.session.selected_segment
            if selection in self.session.unsaved_alignment_edits:
                self.session.revert_segment(*selection)  # type: ignore[arg-type]
            else:
                self.session.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_status_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 10_000)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._shutdown_started:
            event.accept()
            return
        self._shutdown_started = True
        self.timer.stop()
        self.audition_stop_timer.stop()
        self.ab_comparison.close()
        self.audio.stop()
        self.recording.close()
        self.tasks.cancel_all()
        deadline = QtCore.QDeadlineTimer(self.SHUTDOWN_WAIT_MS)
        tasks_done = False
        analysis_done = False
        renders_done = False
        while not deadline.hasExpired():
            remaining = max(0, min(25, deadline.remainingTime()))
            tasks_done = self.tasks.wait(remaining)
            analysis_done = self.timeline.analysis.wait(remaining)
            renders_done = self.audio.renders.wait(remaining)
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
            )
            if tasks_done and analysis_done and renders_done:
                break
        if tasks_done and analysis_done and renders_done and self.services is not None:
            close_services = getattr(self.services, "close", None)
            if callable(close_services):
                close_services()
        if not tasks_done:
            self.tasks.detach_running()
        if not renders_done:
            self.audio.renders.detach_running()
        self.timeline.close(0)
        self.audio.close()
        event.accept()
