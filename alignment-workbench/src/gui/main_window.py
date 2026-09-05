"""First-pass desktop window over the public workbench boundary."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID

from PySide6 import QtCore, QtGui, QtMultimedia, QtWidgets

from application import AnnotationLibraryListItem, RecordingListItem, SyncState
from gui.controller import (
    ApplicationClient,
    ConceptChoice,
    EditableRecording,
    WorkbenchController,
)
from gui.tasks import TaskRunner, TaskSubmitter
from gui.waveform import WaveformEnvelope, WaveformView, load_waveform_envelope
from models import SignalAnnotation, TimeFrequencyBoxGeometry, TimeFrequencyPolygonGeometry

_ITEM_DATA_ROLE = int(QtCore.Qt.ItemDataRole.UserRole)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        api: ApplicationClient,
        *,
        task_runner: TaskSubmitter | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Alignment Workbench")
        self.resize(1_340, 850)
        self._task_runner = task_runner or TaskRunner(self)
        self._waveform_runner = TaskRunner(self, max_threads=1)
        self.controller = WorkbenchController(api, self._task_runner, self)
        self._busy = False
        self._selected_annotation_id: UUID | None = None
        self._waveform_generation = 0
        self._waveform_recording_id: UUID | None = None
        self._shutdown = False

        self._audio_output = QtMultimedia.QAudioOutput(self)
        self._player = QtMultimedia.QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._audio_output.setVolume(0.8)

        self._build_actions()
        self._build_ui()
        self._connect_signals()
        self._update_actions()
        self.controller.refresh_all()

    def _build_actions(self) -> None:
        self.import_action = QtGui.QAction("Import Recording…", self)
        self.import_action.setObjectName("import_action")
        self.import_action.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.open_action = QtGui.QAction("Open Recording", self)
        self.open_action.setObjectName("open_action")
        self.close_action = QtGui.QAction("Close Recording", self)
        self.close_action.setObjectName("close_action")
        self.save_action = QtGui.QAction("Save Revision", self)
        self.save_action.setObjectName("save_action")
        self.save_action.setShortcut(QtGui.QKeySequence.StandardKey.Save)
        self.undo_action = QtGui.QAction("Undo", self)
        self.undo_action.setShortcut(QtGui.QKeySequence.StandardKey.Undo)
        self.redo_action = QtGui.QAction("Redo", self)
        self.redo_action.setShortcut(QtGui.QKeySequence.StandardKey.Redo)
        self.refresh_recordings_action = QtGui.QAction("Refresh Recordings", self)
        self.refresh_libraries_action = QtGui.QAction("Refresh Libraries", self)

        toolbar = self.addToolBar("Recording")
        toolbar.setObjectName("recording_toolbar")
        toolbar.addAction(self.import_action)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.close_action)
        toolbar.addSeparator()
        toolbar.addAction(self.save_action)
        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)

    def _build_ui(self) -> None:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_navigation())
        splitter.addWidget(self._build_workspace())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 1_030])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage("Starting catalog refresh…")

    def _build_navigation(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget(self)
        panel.setMinimumWidth(275)
        layout = QtWidgets.QVBoxLayout(panel)

        recordings_group = QtWidgets.QGroupBox("Recordings", panel)
        recordings_layout = QtWidgets.QVBoxLayout(recordings_group)
        self.recordings_state = QtWidgets.QLabel("Loading…", recordings_group)
        self.recordings_list = QtWidgets.QListWidget(recordings_group)
        self.recordings_list.setObjectName("recordings_list")
        self.recordings_list.setAlternatingRowColors(True)
        recording_buttons = QtWidgets.QHBoxLayout()
        self.open_button = QtWidgets.QPushButton("Open", recordings_group)
        self.refresh_recordings_button = QtWidgets.QPushButton("Refresh", recordings_group)
        recording_buttons.addWidget(self.open_button)
        recording_buttons.addWidget(self.refresh_recordings_button)
        recordings_layout.addWidget(self.recordings_state)
        recordings_layout.addWidget(self.recordings_list, 1)
        recordings_layout.addLayout(recording_buttons)

        libraries_group = QtWidgets.QGroupBox("Annotation libraries", panel)
        libraries_layout = QtWidgets.QVBoxLayout(libraries_group)
        self.libraries_state = QtWidgets.QLabel("Loading…", libraries_group)
        self.libraries_list = QtWidgets.QListWidget(libraries_group)
        self.libraries_list.setObjectName("libraries_list")
        self.libraries_list.setAlternatingRowColors(True)
        library_buttons = QtWidgets.QHBoxLayout()
        self.pin_library_button = QtWidgets.QPushButton("Pin latest", libraries_group)
        self.refresh_libraries_button = QtWidgets.QPushButton("Refresh", libraries_group)
        library_buttons.addWidget(self.pin_library_button)
        library_buttons.addWidget(self.refresh_libraries_button)
        libraries_layout.addWidget(self.libraries_state)
        libraries_layout.addWidget(self.libraries_list, 1)
        libraries_layout.addLayout(library_buttons)

        layout.addWidget(recordings_group, 1)
        layout.addWidget(libraries_group, 1)
        return panel

    def _build_workspace(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(panel)

        self.error_banner = QtWidgets.QLabel(panel)
        self.error_banner.setObjectName("error_banner")
        self.error_banner.setWordWrap(True)
        self.error_banner.setStyleSheet(
            "QLabel { background: #5b2027; color: #ffffff; padding: 7px; border-radius: 3px; }"
        )
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        metadata_group = QtWidgets.QGroupBox("Active recording", panel)
        metadata = QtWidgets.QGridLayout(metadata_group)
        self.name_value = QtWidgets.QLabel("No recording open")
        self.id_value = QtWidgets.QLabel("—")
        self.id_value.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.speaker_value = QtWidgets.QLabel("—")
        self.language_value = QtWidgets.QLabel("—")
        self.duration_value = QtWidgets.QLabel("—")
        self.revision_value = QtWidgets.QLabel("—")
        self.session_state_value = QtWidgets.QLabel("—")
        fields = (
            ("Name", self.name_value),
            ("Stable ID", self.id_value),
            ("Speaker", self.speaker_value),
            ("Language", self.language_value),
            ("Duration / audio", self.duration_value),
            ("Current revision", self.revision_value),
            ("Session", self.session_state_value),
        )
        for index, (label, value) in enumerate(fields):
            row, column = divmod(index, 2)
            metadata.addWidget(QtWidgets.QLabel(f"{label}:"), row, column * 2)
            metadata.addWidget(value, row, column * 2 + 1)
        metadata.setColumnStretch(1, 1)
        metadata.setColumnStretch(3, 1)
        layout.addWidget(metadata_group)

        transport = QtWidgets.QHBoxLayout()
        self.play_pause_button = QtWidgets.QPushButton("Play", panel)
        self.stop_button = QtWidgets.QPushButton("Stop", panel)
        self.playback_time = QtWidgets.QLabel("0.00 / 0.00 s", panel)
        transport.addWidget(self.play_pause_button)
        transport.addWidget(self.stop_button)
        transport.addWidget(self.playback_time)
        transport.addStretch(1)
        layout.addLayout(transport)

        self.waveform = WaveformView(panel)
        self.waveform.setObjectName("waveform")
        layout.addWidget(self.waveform, 2)

        lower = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, panel)
        lower.setChildrenCollapsible(False)
        lower.addWidget(self._build_annotation_table(lower))
        lower.addWidget(self._build_editor(lower))
        lower.setSizes([670, 360])
        layout.addWidget(lower, 3)
        return panel

    def _build_annotation_table(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Annotations", parent)
        layout = QtWidgets.QVBoxLayout(group)
        self.annotation_table = QtWidgets.QTableWidget(0, 6, group)
        self.annotation_table.setObjectName("annotation_table")
        self.annotation_table.setHorizontalHeaderLabels(
            ("Concept", "Geometry", "Start", "End", "Note", "ID")
        )
        self.annotation_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.annotation_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.annotation_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.annotation_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.annotation_table)
        return group

    def _build_editor(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Annotation editor", parent)
        layout = QtWidgets.QVBoxLayout(group)
        form = QtWidgets.QFormLayout()
        self.concept_combo = QtWidgets.QComboBox(group)
        self.concept_combo.setObjectName("concept_combo")
        self.geometry_combo = QtWidgets.QComboBox(group)
        self.start_sample = QtWidgets.QSpinBox(group)
        self.end_sample = QtWidgets.QSpinBox(group)
        self.minimum_frequency = QtWidgets.QDoubleSpinBox(group)
        self.maximum_frequency = QtWidgets.QDoubleSpinBox(group)
        self.minimum_frequency.setRange(0.0, 1_000_000.0)
        self.maximum_frequency.setRange(0.01, 1_000_000.0)
        self.maximum_frequency.setValue(8_000.0)
        self.note_edit = QtWidgets.QLineEdit(group)
        form.addRow("Concept", self.concept_combo)
        form.addRow("Geometry", self.geometry_combo)
        form.addRow("Start sample", self.start_sample)
        form.addRow("End sample", self.end_sample)
        form.addRow("Minimum Hz", self.minimum_frequency)
        form.addRow("Maximum Hz", self.maximum_frequency)
        form.addRow("Note", self.note_edit)
        layout.addLayout(form)

        buttons = QtWidgets.QHBoxLayout()
        self.create_annotation_button = QtWidgets.QPushButton("Create", group)
        self.create_annotation_button.setObjectName("create_annotation_button")
        self.update_annotation_button = QtWidgets.QPushButton("Update selected", group)
        self.delete_annotation_button = QtWidgets.QPushButton("Delete", group)
        buttons.addWidget(self.create_annotation_button)
        buttons.addWidget(self.update_annotation_button)
        buttons.addWidget(self.delete_annotation_button)
        layout.addLayout(buttons)

        self.annotation_details = QtWidgets.QPlainTextEdit(group)
        self.annotation_details.setReadOnly(True)
        self.annotation_details.setPlaceholderText("Select an annotation to inspect it")
        layout.addWidget(QtWidgets.QLabel("Selected annotation"))
        layout.addWidget(self.annotation_details, 1)

        self.pinned_libraries = QtWidgets.QListWidget(group)
        self.pinned_libraries.setMaximumHeight(100)
        layout.addWidget(QtWidgets.QLabel("Pinned libraries"))
        layout.addWidget(self.pinned_libraries)

        save_form = QtWidgets.QFormLayout()
        self.author_edit = QtWidgets.QLineEdit(group)
        self.save_message_edit = QtWidgets.QLineEdit(group)
        self.save_message_edit.setPlaceholderText("Optional revision message")
        save_form.addRow("Author", self.author_edit)
        save_form.addRow("Save message", self.save_message_edit)
        layout.addLayout(save_form)
        return group

    def _connect_signals(self) -> None:
        self.import_action.triggered.connect(self._import_recording)
        self.open_action.triggered.connect(self._open_selected_recording)
        self.close_action.triggered.connect(self._close_active_recording)
        self.save_action.triggered.connect(self._save)
        self.undo_action.triggered.connect(self.controller.undo)
        self.redo_action.triggered.connect(self.controller.redo)
        self.refresh_recordings_action.triggered.connect(self.controller.refresh_recordings)
        self.refresh_libraries_action.triggered.connect(self.controller.refresh_libraries)
        self.open_button.clicked.connect(self._open_selected_recording)
        self.refresh_recordings_button.clicked.connect(self.controller.refresh_recordings)
        self.refresh_libraries_button.clicked.connect(self.controller.refresh_libraries)
        self.pin_library_button.clicked.connect(self._pin_selected_library)
        self.recordings_list.itemDoubleClicked.connect(self._recording_double_clicked)
        self.recordings_list.itemSelectionChanged.connect(self._update_actions)
        self.libraries_list.itemSelectionChanged.connect(self._update_actions)

        self.controller.recordings_loading.connect(
            lambda: self.recordings_state.setText("Loading…")
        )
        self.controller.recordings_changed.connect(self._recordings_changed)
        self.controller.recordings_failed.connect(self._recordings_failed)
        self.controller.libraries_loading.connect(lambda: self.libraries_state.setText("Loading…"))
        self.controller.libraries_changed.connect(self._libraries_changed)
        self.controller.libraries_failed.connect(self._libraries_failed)
        self.controller.session_changed.connect(self._session_changed)
        self.controller.busy_changed.connect(self._busy_changed)
        self.controller.error.connect(self._show_error)
        self.controller.notice.connect(self._show_notice)

        self.annotation_table.itemSelectionChanged.connect(self._annotation_selection_changed)
        self.waveform.annotation_selected.connect(self._select_annotation)
        self.concept_combo.currentIndexChanged.connect(self._concept_changed)
        self.geometry_combo.currentIndexChanged.connect(self._geometry_changed)
        self.create_annotation_button.clicked.connect(self._create_annotation)
        self.update_annotation_button.clicked.connect(self._update_annotation)
        self.delete_annotation_button.clicked.connect(self._delete_annotation)

        self.play_pause_button.clicked.connect(self._toggle_playback)
        self.stop_button.clicked.connect(self._stop_playback)
        self._player.positionChanged.connect(self._playback_position_changed)
        self._player.durationChanged.connect(self._playback_duration_changed)
        self._player.playbackStateChanged.connect(self._playback_state_changed)
        self._player.errorOccurred.connect(self._playback_error)

    @QtCore.Slot(object)
    def _recordings_changed(self, value: object) -> None:
        recordings = cast(tuple[RecordingListItem, ...], value)
        selected_id = self.controller.session.recording_id if self.controller.session else None
        self.recordings_list.clear()
        for recording in recordings:
            speaker = (
                f" · {recording.speaker_display_name}"
                if recording.speaker_display_name
                else ""
            )
            item = QtWidgets.QListWidgetItem(
                f"{recording.display_name}{speaker}\n"
                f"r{recording.revision_number} · {recording.duration_seconds:.2f}s · "
                f"{recording.audio_status.value}"
            )
            item.setData(_ITEM_DATA_ROLE, recording)
            item.setToolTip(str(recording.recording_id))
            self.recordings_list.addItem(item)
            if recording.recording_id == selected_id:
                self.recordings_list.setCurrentItem(item)
        self.recordings_state.setText(
            f"{len(recordings)} recording{'s' if len(recordings) != 1 else ''}"
            if recordings
            else "No recordings"
        )
        self._update_actions()

    @QtCore.Slot(str)
    def _recordings_failed(self, message: str) -> None:
        self.recordings_list.clear()
        self.recordings_state.setText(f"Error: {message}")
        self._show_error(f"Could not refresh recordings: {message}")

    @QtCore.Slot(object)
    def _libraries_changed(self, value: object) -> None:
        libraries = cast(tuple[AnnotationLibraryListItem, ...], value)
        self.libraries_list.clear()
        for library in libraries:
            version = library.latest_version_label or "no published version"
            item = QtWidgets.QListWidgetItem(
                f"{library.name}\n{library.namespace}@{version} · {library.entry_count} entries"
            )
            item.setData(_ITEM_DATA_ROLE, library)
            item.setToolTip(str(library.library_id))
            self.libraries_list.addItem(item)
        self.libraries_state.setText(
            f"{len(libraries)} librar{'ies' if len(libraries) != 1 else 'y'}"
            if libraries
            else "No annotation libraries"
        )
        self._update_actions()

    @QtCore.Slot(str)
    def _libraries_failed(self, message: str) -> None:
        self.libraries_list.clear()
        self.libraries_state.setText(f"Error: {message}")
        self._show_error(f"Could not refresh libraries: {message}")

    @QtCore.Slot(object)
    def _session_changed(self, value: object) -> None:
        session = cast(EditableRecording | None, value)
        if session is None:
            self._selected_annotation_id = None
            self.annotation_table.clearSelection()
            self._clear_workspace()
            self._update_actions()
            return
        annotation_ids = {annotation.id for annotation in session.annotations}
        if self._selected_annotation_id not in annotation_ids:
            self._selected_annotation_id = None
            self.annotation_table.clearSelection()
        self._refresh_workspace(session)
        if self._waveform_recording_id != session.recording_id:
            self._prepare_waveform(session)
        else:
            asset = session.audio.asset
            self.waveform.set_annotations(
                session.annotations,
                frame_count=asset.frame_count,
                sample_rate_hz=asset.sample_rate_hz,
            )
        self._update_actions()

    def _refresh_workspace(self, session: EditableRecording) -> None:
        item = self.controller.catalog_item
        asset = session.audio.asset
        self.name_value.setText(session.name)
        self.id_value.setText(str(session.recording_id))
        self.speaker_value.setText(
            item.speaker_display_name
            if item is not None and item.speaker_display_name
            else (
                str(session.default_speaker_ref)
                if session.default_speaker_ref is not None
                else "Not assigned"
            )
        )
        self.language_value.setText(session.language)
        self.duration_value.setText(
            f"{asset.duration_seconds:.3f}s · {asset.sample_rate_hz} Hz · "
            f"{asset.channels} channel(s) · available"
        )
        revision_number = f"r{item.revision_number} · " if item is not None else ""
        self.revision_value.setText(f"{revision_number}{session.base_revision_id}")
        cleanliness = "dirty" if session.dirty else "clean"
        self.session_state_value.setText(f"{cleanliness} · {session.sync_state.value}")
        maximum_frame = min(asset.frame_count, 2_147_483_647)
        self.start_sample.setRange(0, maximum_frame - 1)
        self.end_sample.setRange(1, maximum_frame)
        if self.end_sample.value() <= self.start_sample.value():
            self.end_sample.setValue(min(maximum_frame, self.start_sample.value() + 1))
        self._refresh_pinned_libraries(session)
        self._refresh_concepts()
        self._refresh_annotation_table(session)
        self._set_audio_source(session.audio.local_path)

    def _refresh_pinned_libraries(self, session: EditableRecording) -> None:
        catalog = {item.library_id: item for item in self.controller.libraries}
        self.pinned_libraries.clear()
        for version in session.pinned_libraries:
            library = catalog.get(version.library_id)
            namespace = library.namespace if library is not None else str(version.library_id)
            self.pinned_libraries.addItem(f"{namespace}@{version.version_label}")
        if not session.pinned_libraries:
            item = QtWidgets.QListWidgetItem("No libraries pinned")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.pinned_libraries.addItem(item)

    def _refresh_concepts(self) -> None:
        previous = self._current_concept_reference()
        self.concept_combo.blockSignals(True)
        self.concept_combo.clear()
        for choice in self.controller.available_concepts:
            self.concept_combo.addItem(choice.display_text, choice)
            if previous is not None and choice.reference == previous:
                self.concept_combo.setCurrentIndex(self.concept_combo.count() - 1)
        self.concept_combo.blockSignals(False)
        self._concept_changed()

    def _refresh_annotation_table(self, session: EditableRecording) -> None:
        selected_id = self._selected_annotation_id
        self.annotation_table.setRowCount(len(session.annotations))
        for row, annotation in enumerate(session.annotations):
            geometry = annotation.geometry
            values = (
                str(annotation.concept_ref),
                geometry.type,
                str(geometry.start_sample),
                "—" if geometry.end_sample is None else str(geometry.end_sample),
                annotation.note or "",
                str(annotation.id),
            )
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    cell.setData(_ITEM_DATA_ROLE, annotation.id)
                self.annotation_table.setItem(row, column, cell)
            if annotation.id == selected_id:
                self.annotation_table.selectRow(row)
        self.waveform.set_selected_annotation(selected_id)

    def _prepare_waveform(self, session: EditableRecording) -> None:
        self._waveform_generation += 1
        generation = self._waveform_generation
        recording_id = session.recording_id
        path = session.audio.local_path
        self._waveform_recording_id = recording_id
        self.waveform.set_loading()

        def success(envelope: WaveformEnvelope) -> None:
            current = self.controller.session
            if (
                generation != self._waveform_generation
                or current is None
                or current.recording_id != recording_id
            ):
                return
            self.waveform.set_recording(envelope, current.annotations)

        def failure(exc: BaseException) -> None:
            current = self.controller.session
            if (
                generation == self._waveform_generation
                and current is not None
                and current.recording_id == recording_id
            ):
                self.waveform.set_error(f"Waveform unavailable: {exc}")

        self._waveform_runner.submit(lambda: load_waveform_envelope(path), success, failure)

    def _clear_workspace(self) -> None:
        self.name_value.setText("No recording open")
        for label in (
            self.id_value,
            self.speaker_value,
            self.language_value,
            self.duration_value,
            self.revision_value,
            self.session_state_value,
        ):
            label.setText("—")
        self.annotation_table.setRowCount(0)
        self.annotation_details.clear()
        self.concept_combo.clear()
        self.geometry_combo.clear()
        self.pinned_libraries.clear()
        self._waveform_generation += 1
        self._waveform_recording_id = None
        self.waveform.clear()
        self._player.stop()
        self._player.setSource(QtCore.QUrl())
        self.playback_time.setText("0.00 / 0.00 s")

    @QtCore.Slot(bool)
    def _busy_changed(self, busy: bool) -> None:
        self._busy = busy
        self._update_actions()
        if busy:
            self.statusBar().showMessage("Working…")

    @QtCore.Slot(str)
    def _show_error(self, message: str) -> None:
        self.error_banner.setText(message)
        self.error_banner.show()
        self.statusBar().showMessage(message, 10_000)

    @QtCore.Slot(str)
    def _show_notice(self, message: str) -> None:
        self.error_banner.hide()
        self.statusBar().showMessage(message, 5_000)

    def _update_actions(self) -> None:
        session = self.controller.session
        has_session = session is not None
        recording_selected = self.recordings_list.currentItem() is not None
        library = self._selected_library()
        editable = has_session and not self._busy
        self.import_action.setEnabled(not self._busy)
        self.open_action.setEnabled(recording_selected and not self._busy)
        self.open_button.setEnabled(recording_selected and not self._busy)
        self.close_action.setEnabled(has_session and not self._busy)
        self.save_action.setEnabled(
            bool(editable and session is not None and session.sync_state is not SyncState.CONFLICT)
        )
        self.undo_action.setEnabled(bool(editable and session is not None and session.can_undo))
        self.redo_action.setEnabled(bool(editable and session is not None and session.can_redo))
        self.refresh_recordings_button.setEnabled(not self._busy)
        self.refresh_libraries_button.setEnabled(not self._busy)
        self.pin_library_button.setEnabled(
            bool(editable and library is not None and library.latest_version_id is not None)
        )
        has_concept = self._current_concept_choice() is not None
        has_selection = self._selected_annotation_id is not None
        self.create_annotation_button.setEnabled(bool(editable and has_concept))
        self.update_annotation_button.setEnabled(bool(editable and has_concept and has_selection))
        self.delete_annotation_button.setEnabled(bool(editable and has_selection))
        self.play_pause_button.setEnabled(has_session)
        self.stop_button.setEnabled(has_session)

    @QtCore.Slot()
    def _open_selected_recording(self) -> None:
        item = self.recordings_list.currentItem()
        if item is None or not self._prepare_to_replace_session():
            return
        recording = cast(RecordingListItem, item.data(_ITEM_DATA_ROLE))
        self.controller.open_recording(recording)

    @QtCore.Slot(QtWidgets.QListWidgetItem)
    def _recording_double_clicked(self, item: QtWidgets.QListWidgetItem) -> None:
        self.recordings_list.setCurrentItem(item)
        self._open_selected_recording()

    @QtCore.Slot()
    def _import_recording(self) -> None:
        filename, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Recording",
            "",
            "PCM WAV audio (*.wav);;All files (*)",
        )
        if not filename:
            return
        source = Path(filename)
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Recording name",
            "Display name:",
            text=source.stem,
        )
        if not accepted or not name.strip():
            return
        language, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Recording language",
            "Language tag:",
            text="und",
        )
        if not accepted or not language.strip() or not self._prepare_to_replace_session():
            return
        self.controller.import_recording(source, name=name.strip(), language=language.strip())

    def _prepare_to_replace_session(self) -> bool:
        session = self.controller.session
        if session is None:
            return True
        discard = False
        if session.dirty:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Unsaved changes",
                "Discard unsaved changes and close the active recording?",
                QtWidgets.QMessageBox.StandardButton.Discard
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Discard:
                return False
            discard = True
        return self.controller.close_session(discard_unsaved_changes=discard)

    @QtCore.Slot()
    def _close_active_recording(self) -> None:
        self._prepare_to_replace_session()

    @QtCore.Slot()
    def _pin_selected_library(self) -> None:
        library = self._selected_library()
        if library is not None:
            self.controller.pin_latest_library(library)

    def _selected_library(self) -> AnnotationLibraryListItem | None:
        item = self.libraries_list.currentItem()
        if item is None:
            return None
        return cast(AnnotationLibraryListItem, item.data(_ITEM_DATA_ROLE))

    @QtCore.Slot()
    def _concept_changed(self) -> None:
        choice = self._current_concept_choice()
        previous = self.geometry_combo.currentData()
        self.geometry_combo.blockSignals(True)
        self.geometry_combo.clear()
        if choice is not None:
            for geometry_type in choice.entry.allowed_geometry_types:
                self.geometry_combo.addItem(geometry_type.replace("_", " ").title(), geometry_type)
                if geometry_type == previous:
                    self.geometry_combo.setCurrentIndex(self.geometry_combo.count() - 1)
        self.geometry_combo.blockSignals(False)
        self._geometry_changed()
        self._update_actions()

    @QtCore.Slot()
    def _geometry_changed(self) -> None:
        geometry_type = self.geometry_combo.currentData()
        is_point = geometry_type == "point"
        has_frequency = geometry_type in {
            "time_frequency_box",
            "time_frequency_polygon",
        }
        self.end_sample.setEnabled(not is_point)
        self.minimum_frequency.setEnabled(has_frequency)
        self.maximum_frequency.setEnabled(has_frequency)

    def _current_concept_choice(self) -> ConceptChoice | None:
        value = self.concept_combo.currentData()
        return value if isinstance(value, ConceptChoice) else None

    def _current_concept_reference(self) -> object | None:
        choice = self._current_concept_choice()
        return choice.reference if choice is not None else None

    @QtCore.Slot()
    def _create_annotation(self) -> None:
        choice = self._current_concept_choice()
        geometry_type = self.geometry_combo.currentData()
        if choice is None or not isinstance(geometry_type, str):
            return
        self.controller.create_annotation(
            concept_ref=choice.reference,
            geometry_type=geometry_type,
            start_sample=self.start_sample.value(),
            end_sample=self.end_sample.value(),
            min_frequency_hz=self.minimum_frequency.value(),
            max_frequency_hz=self.maximum_frequency.value(),
            note=self.note_edit.text().strip() or None,
        )

    @QtCore.Slot()
    def _update_annotation(self) -> None:
        choice = self._current_concept_choice()
        annotation_id = self._selected_annotation_id
        if choice is None or annotation_id is None:
            return
        self.controller.update_annotation(
            annotation_id,
            concept_ref=choice.reference,
            start_sample=self.start_sample.value(),
            end_sample=self.end_sample.value(),
            min_frequency_hz=self.minimum_frequency.value(),
            max_frequency_hz=self.maximum_frequency.value(),
            note=self.note_edit.text().strip() or None,
        )

    @QtCore.Slot()
    def _delete_annotation(self) -> None:
        if self._selected_annotation_id is not None:
            self.controller.delete_annotation(self._selected_annotation_id)

    @QtCore.Slot()
    def _annotation_selection_changed(self) -> None:
        row = self.annotation_table.currentRow()
        session = self.controller.session
        if row < 0 or session is None:
            self._selected_annotation_id = None
            self.annotation_details.clear()
            self.waveform.set_selected_annotation(None)
            self._update_actions()
            return
        item = self.annotation_table.item(row, 0)
        annotation_id = item.data(_ITEM_DATA_ROLE) if item is not None else None
        if not isinstance(annotation_id, UUID):
            return
        self._selected_annotation_id = annotation_id
        annotation = session.get_annotation(annotation_id)
        self._populate_editor(annotation)
        self.waveform.set_selected_annotation(annotation_id)
        self._update_actions()

    def _populate_editor(self, annotation: SignalAnnotation) -> None:
        for index in range(self.concept_combo.count()):
            choice = self.concept_combo.itemData(index)
            if isinstance(choice, ConceptChoice) and choice.reference == annotation.concept_ref:
                self.concept_combo.setCurrentIndex(index)
                break
        for index in range(self.geometry_combo.count()):
            if self.geometry_combo.itemData(index) == annotation.geometry.type:
                self.geometry_combo.setCurrentIndex(index)
                break
        geometry = annotation.geometry
        self.start_sample.setValue(geometry.start_sample)
        default_end = min(geometry.start_sample + 1, 2_147_483_647)
        self.end_sample.setValue(geometry.end_sample or default_end)
        if isinstance(geometry, (TimeFrequencyBoxGeometry, TimeFrequencyPolygonGeometry)):
            self.minimum_frequency.setValue(geometry.min_frequency_hz)
            self.maximum_frequency.setValue(geometry.max_frequency_hz)
        self.note_edit.setText(annotation.note or "")
        self.annotation_details.setPlainText(annotation.to_deterministic_json())

    @QtCore.Slot(object)
    def _select_annotation(self, value: object) -> None:
        if not isinstance(value, UUID):
            return
        for row in range(self.annotation_table.rowCount()):
            item = self.annotation_table.item(row, 0)
            if item is not None and item.data(_ITEM_DATA_ROLE) == value:
                self.annotation_table.selectRow(row)
                return

    @QtCore.Slot()
    def _save(self) -> None:
        self.controller.save(
            author=self.author_edit.text().strip() or None,
            message=self.save_message_edit.text().strip() or None,
        )

    def _set_audio_source(self, path: Path) -> None:
        expected = QtCore.QUrl.fromLocalFile(str(path))
        if self._player.source() != expected:
            self._player.stop()
            self._player.setSource(expected)

    @QtCore.Slot()
    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @QtCore.Slot()
    def _stop_playback(self) -> None:
        self._player.stop()
        self._player.setPosition(0)

    @QtCore.Slot(int)
    def _playback_position_changed(self, position_ms: int) -> None:
        duration_ms = self._player.duration()
        self.playback_time.setText(f"{position_ms / 1000:.2f} / {duration_ms / 1000:.2f} s")
        self.waveform.set_playhead_seconds(position_ms / 1000)

    @QtCore.Slot(int)
    def _playback_duration_changed(self, duration_ms: int) -> None:
        self.playback_time.setText(
            f"{self._player.position() / 1000:.2f} / {duration_ms / 1000:.2f} s"
        )

    @QtCore.Slot(object)
    def _playback_state_changed(self, state: object) -> None:
        self.play_pause_button.setText(
            "Pause"
            if state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            else "Play"
        )

    @QtCore.Slot(object, str)
    def _playback_error(self, _error: object, message: str) -> None:
        if message:
            self._show_error(f"Audio playback failed: {message}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._busy:
            self._show_error("Wait for the current operation to finish before exiting")
            event.ignore()
            return
        if not self._prepare_to_replace_session():
            event.ignore()
            return
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._player.stop()
        self.controller.shutdown()
        self._waveform_runner.shutdown()
        shutdown = getattr(self._task_runner, "shutdown", None)
        if callable(shutdown):
            shutdown()
