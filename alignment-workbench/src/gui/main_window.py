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
from gui.spectrogram import SpectrogramPreview, load_spectrogram
from gui.tasks import TaskRunner, TaskSubmitter
from gui.waveform import (
    WaveformEnvelope,
    WaveformView,
    annotation_label,
    load_waveform_detail,
    load_waveform_envelope,
)
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
        self._exit_requested = False
        self._detail_pending = False
        self._spectrogram_pending = False
        self._detail_timer = QtCore.QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(120)
        self._detail_timer.timeout.connect(self._load_detail)
        self._detail_timer.timeout.connect(self._load_spectrogram)
        self._library_status = "loading"
        self._playback_failure = ""
        self._pending_seek: int | None = None
        self._media_devices = QtMultimedia.QMediaDevices(self)

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
        self.retry_saves_action = QtGui.QAction("Retry Pending Saves", self)
        self.retry_saves_action.triggered.connect(self.controller.retry_recovery)
        self.controller.save_finished.connect(self._exit_save_finished)
        self.controller.recovery_finished.connect(self._exit_recovery_finished)
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
        toolbar.addAction(self.retry_saves_action)
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
        panel.setMinimumWidth(210)
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

        self.error_panel = QtWidgets.QWidget(panel)
        self.error_panel.setObjectName("error_panel")
        self.error_panel.setStyleSheet(
            "QWidget#error_panel { background: #5b2027; border-radius: 3px; }"
        )
        error_layout = QtWidgets.QHBoxLayout(self.error_panel)
        error_layout.setContentsMargins(7, 7, 7, 7)
        self.error_banner = QtWidgets.QLabel(self.error_panel)
        self.error_banner.setObjectName("error_banner")
        self.error_banner.setWordWrap(True)
        self.error_banner.setMaximumHeight(90)
        self.error_banner.setStyleSheet(
            "QLabel { background: #5b2027; color: #ffffff; padding: 7px; border-radius: 3px; }"
        )
        error_layout.addWidget(self.error_banner, 1)
        self.error_retry_button = QtWidgets.QToolButton(self.error_panel)
        self.error_retry_button.setObjectName("error_retry_button")
        self.error_retry_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.error_retry_button.setDefaultAction(self.retry_saves_action)
        self.error_retry_button.hide()
        error_layout.addWidget(self.error_retry_button)
        self.error_panel.hide()
        layout.addWidget(self.error_panel)
        self.controller.recovery_error.connect(
            lambda message: self._show_error(message, retry=True)
        )

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
        self.metadata_toggle = QtWidgets.QToolButton(panel)
        self.metadata_toggle.setText("Recording details")
        self.metadata_toggle.setCheckable(True)
        self.metadata_toggle.toggled.connect(metadata_group.setVisible)
        metadata_group.hide()
        summary = QtWidgets.QHBoxLayout()
        summary.addWidget(self.name_value, 1)
        summary.addWidget(self.session_state_value)
        summary.addWidget(self.metadata_toggle)
        layout.addLayout(summary)
        layout.addWidget(metadata_group)

        transport = QtWidgets.QHBoxLayout()
        self.play_pause_button = QtWidgets.QPushButton("Play", panel)
        self.stop_button = QtWidgets.QPushButton("Stop", panel)
        self.playback_time = QtWidgets.QLabel("0.00 / 0.00 s", panel)
        self.playback_status = QtWidgets.QLabel("No audio loaded", panel)
        self.playback_status.setWordWrap(True)
        transport.addWidget(self.play_pause_button)
        transport.addWidget(self.stop_button)
        transport.addWidget(self.playback_time)
        transport.addStretch(1)
        transport.addWidget(QtWidgets.QLabel("Audio view:", panel))
        self.audio_view_toggle = QtWidgets.QComboBox(panel)
        self.audio_view_toggle.setObjectName("audio_view_toggle")
        self.audio_view_toggle.setAccessibleName("Audio view")
        self.audio_view_toggle.addItems(["Waveform", "Spectrogram"])
        self.audio_view_toggle.setToolTip(
            "Switch audio display; brighter colors show stronger energy."
        )
        transport.addWidget(self.audio_view_toggle)
        layout.addLayout(transport)
        layout.addWidget(self.playback_status)

        self.waveform = WaveformView(panel)
        self.waveform.setObjectName("waveform")
        navigation = QtWidgets.QHBoxLayout()
        for text, callback in (
            ("Fit recording", self.waveform.fit_recording),
            ("Zoom to selection", self.waveform.zoom_to_selection),
            ("Zoom +", lambda: self.waveform.zoom(0.5)),
            ("Zoom -", lambda: self.waveform.zoom(2)),
            ("Clear selection", self.waveform.clear_selection),
        ):
            button = QtWidgets.QPushButton(text, panel)
            button.clicked.connect(callback)
            navigation.addWidget(button)
        layout.addLayout(navigation)
        self.selection_readout = QtWidgets.QLabel(
            "Drag across the audio view to select an interval."
        )
        self.selection_readout.setWordWrap(True)
        layout.addWidget(self.selection_readout)
        self.workspace_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical, panel)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(self.waveform)

        lower = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, panel)
        lower.setChildrenCollapsible(False)
        lower.addWidget(self._build_annotation_table(lower))
        lower.addWidget(self._build_editor(lower))
        lower.setSizes([420, 420])
        self.workspace_splitter.addWidget(lower)
        self.workspace_splitter.setSizes([280, 400])
        layout.addWidget(self.workspace_splitter, 1)
        return panel

    def _build_annotation_table(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        group = QtWidgets.QGroupBox("Annotations", parent)
        layout = QtWidgets.QVBoxLayout(group)
        self.annotation_table = QtWidgets.QTableWidget(0, 7, group)
        self.annotation_table.setObjectName("annotation_table")
        self.annotation_table.setHorizontalHeaderLabels(
            ("Concept", "Geometry", "Start (s)", "End (s)", "Note", "ID", "Label")
        )
        self.annotation_table.setColumnHidden(1, True)
        self.annotation_table.setColumnHidden(5, True)
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
        header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.moveSection(6, 0)
        layout.addWidget(self.annotation_table)
        return group

    def _build_editor(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self.editor_scroll = QtWidgets.QScrollArea(parent)
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setMinimumWidth(300)
        group = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(group)
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        self.new_annotation_button = QtWidgets.QPushButton("New annotation", group)
        self.new_annotation_button.clicked.connect(self._new_annotation)
        layout.addWidget(self.new_annotation_button)
        self.editor_mode = QtWidgets.QLabel("New annotation", group)
        self.editor_mode.setWordWrap(True)
        layout.addWidget(self.editor_mode)
        self.concept_status = QtWidgets.QLabel(group)
        self.concept_status.setWordWrap(True)
        layout.addWidget(self.concept_status)
        self.use_core_button = QtWidgets.QPushButton("Use core library (core@0.1)", group)
        self.use_core_button.clicked.connect(self.controller.use_core_library)
        layout.addWidget(self.use_core_button)
        self.editor_form = form = QtWidgets.QFormLayout()
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.concept_search = QtWidgets.QLineEdit(group)
        self.concept_search.setPlaceholderText("Search name, IPA symbol, or alias")
        self.category_filter = QtWidgets.QComboBox(group)
        self.category_filter.addItem("All categories", "")
        self.concept_combo = QtWidgets.QComboBox(group)
        self.concept_combo.setObjectName("concept_combo")
        self.concept_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.concept_combo.setMinimumContentsLength(15)
        self.geometry_combo = QtWidgets.QComboBox(group)
        self.start_sample = QtWidgets.QSpinBox(group)
        self.end_sample = QtWidgets.QSpinBox(group)
        self.minimum_frequency = QtWidgets.QDoubleSpinBox(group)
        self.maximum_frequency = QtWidgets.QDoubleSpinBox(group)
        self.minimum_frequency.setRange(0.0, 1_000_000.0)
        self.maximum_frequency.setRange(0.01, 1_000_000.0)
        self.maximum_frequency.setValue(8_000.0)
        self.label_edit = QtWidgets.QLineEdit(group)
        self.label_edit.setObjectName("annotation_label")
        self.label_edit.setPlaceholderText("Displayed on the annotation bar")
        self.note_edit = QtWidgets.QLineEdit(group)
        self.boundary_readout = QtWidgets.QLabel(group)
        self.boundary_readout.setWordWrap(True)
        form.addRow("Label (optional)", self.label_edit)
        form.addRow("Search", self.concept_search)
        form.addRow("Category", self.category_filter)
        form.addRow("Concept", self.concept_combo)
        form.addRow("Geometry", self.geometry_combo)
        form.addRow("Start sample", self.start_sample)
        form.addRow("End sample", self.end_sample)
        form.addRow(self.boundary_readout)
        form.addRow("Minimum Hz", self.minimum_frequency)
        form.addRow("Maximum Hz", self.maximum_frequency)
        form.addRow("Note (optional)", self.note_edit)
        layout.addLayout(form)
        self.start_sample.valueChanged.connect(self._boundary_readout_changed)
        self.end_sample.valueChanged.connect(self._boundary_readout_changed)
        self.create_annotation_button = QtWidgets.QPushButton("Create annotation", group)
        self.create_annotation_button.setObjectName("create_annotation_button")
        self.update_annotation_button = QtWidgets.QPushButton("Apply edits", group)
        self.delete_annotation_button = QtWidgets.QPushButton("Delete selected", group)
        for button in (
            self.create_annotation_button,
            self.update_annotation_button,
            self.delete_annotation_button,
        ):
            layout.addWidget(button)
        self.annotation_details = QtWidgets.QPlainTextEdit(group)
        self.annotation_details.setReadOnly(True)
        self.annotation_details.setMinimumHeight(150)
        details_toggle = QtWidgets.QToolButton(group)
        details_toggle.setText("Annotation JSON details")
        details_toggle.setCheckable(True)
        details_toggle.toggled.connect(self.annotation_details.setVisible)
        layout.addWidget(details_toggle)
        layout.addWidget(self.annotation_details)
        self.annotation_details.hide()
        self.pinned_libraries = QtWidgets.QListWidget(group)
        self.pinned_libraries.setMinimumHeight(60)
        self.pinned_libraries.setMaximumHeight(90)
        layout.addWidget(QtWidgets.QLabel("Pinned libraries"))
        layout.addWidget(self.pinned_libraries)
        layout.addStretch(1)
        self.editor_scroll.setWidget(group)

        self.save_dialog = QtWidgets.QDialog(self)
        self.save_dialog.setWindowTitle("Save revision")
        save_form = QtWidgets.QFormLayout(self.save_dialog)
        self.author_edit = QtWidgets.QLineEdit(self.save_dialog)
        self.save_message_edit = QtWidgets.QLineEdit(self.save_dialog)
        self.save_message_edit.setPlaceholderText("Optional revision message")
        save_form.addRow("Author (optional)", self.author_edit)
        save_form.addRow("Message (optional)", self.save_message_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel,
            self.save_dialog,
        )
        buttons.accepted.connect(self.save_dialog.accept)
        buttons.rejected.connect(self.save_dialog.reject)
        save_form.addRow(buttons)
        return self.editor_scroll

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
        self.controller.libraries_loading.connect(self._libraries_loading)
        self.controller.libraries_changed.connect(self._libraries_changed)
        self.controller.libraries_failed.connect(self._libraries_failed)
        self.controller.session_changed.connect(self._session_changed)
        self.controller.busy_changed.connect(self._busy_changed)
        self.controller.error.connect(self._show_error)
        self.controller.notice.connect(self._show_notice)

        self.annotation_table.itemSelectionChanged.connect(self._annotation_selection_changed)
        self.waveform.annotation_selected.connect(self._select_annotation)
        self.waveform.selection_changed.connect(self._selection_changed)
        self.waveform.seek_requested.connect(self._seek_sample)
        self.waveform.boundary_committed.connect(self.controller.move_boundary)
        self.waveform.viewport_changed.connect(lambda *_: self._detail_timer.start())
        self.audio_view_toggle.currentIndexChanged.connect(self._audio_view_changed)
        self.controller.annotation_created.connect(self._annotation_created)
        self.concept_search.textChanged.connect(self._refresh_concepts)
        self.category_filter.currentIndexChanged.connect(self._refresh_concepts)
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
        self._player.mediaStatusChanged.connect(lambda _: self._update_playback())
        self._player.seekableChanged.connect(lambda _: self._update_playback())
        self._media_devices.audioOutputsChanged.connect(self._update_playback)

    @QtCore.Slot(object)
    def _recordings_changed(self, value: object) -> None:
        recordings = cast(tuple[RecordingListItem, ...], value)
        selected_id = self.controller.session.recording_id if self.controller.session else None
        self.recordings_list.clear()
        for recording in recordings:
            speaker = (
                f" · {recording.speaker_display_name}" if recording.speaker_display_name else ""
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
        self._library_status = "ready"
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
            else "No published libraries"
        )
        self._update_actions()

    @QtCore.Slot(str)
    def _libraries_failed(self, message: str) -> None:
        self._library_status = "failed"
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
            with QtCore.QSignalBlocker(self.annotation_table):
                self.annotation_table.clearSelection()
        self.waveform.definitions = {
            c.reference: c.entry for c in self.controller.available_concepts
        }
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
        choices = self.controller.available_concepts
        category = self.category_filter.currentData()
        with QtCore.QSignalBlocker(self.category_filter):
            self.category_filter.clear()
            self.category_filter.addItem("All categories", "")
            descriptors = {}
            for choice in choices:
                key = choice.entry.metadata.get("category")
                descriptor = choice.entry.metadata.get("category_descriptor", {})
                if isinstance(key, str):
                    name = descriptor.get("name", key) if isinstance(descriptor, dict) else key
                    descriptors[key] = name if isinstance(name, str) else key
            for key, name in sorted(descriptors.items()):
                self.category_filter.addItem(name, key)
            self.category_filter.setCurrentIndex(max(0, self.category_filter.findData(category)))
        category = self.category_filter.currentData()
        query = self.concept_search.text().strip().casefold()
        for choice in choices:
            if self._selected_annotation_id is None:
                if category and choice.entry.metadata.get("category") != category:
                    continue
                searchable = (
                    choice.display_text + " " + str(choice.entry.metadata.get("aliases", ""))
                )
                if query and query not in searchable.casefold():
                    continue
            self.concept_combo.addItem(choice.display_text, choice)
            if previous is not None and choice.reference == previous:
                self.concept_combo.setCurrentIndex(self.concept_combo.count() - 1)
        self.concept_combo.blockSignals(False)
        self._concept_changed()

    def _refresh_annotation_table(self, session: EditableRecording) -> None:
        selected_id = self._selected_annotation_id
        blocker = QtCore.QSignalBlocker(self.annotation_table)
        self.annotation_table.clearSelection()
        self.annotation_table.setRowCount(len(session.annotations))
        for row, annotation in enumerate(session.annotations):
            geometry = annotation.geometry
            values = (
                annotation.concept_ref.entry_key,
                geometry.type,
                f"{geometry.start_sample / session.audio.asset.sample_rate_hz:.4f}",
                "—"
                if geometry.end_sample is None
                else f"{geometry.end_sample / session.audio.asset.sample_rate_hz:.4f}",
                annotation.note or "",
                str(annotation.id),
                annotation_label(annotation, self.waveform.definitions),
            )
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                cell.setToolTip(
                    f"{annotation.concept_ref}\nSamples: {geometry.start_sample} - "
                    f"{geometry.end_sample}\n{annotation.note or ''}"
                )
                if column == 0:
                    cell.setData(_ITEM_DATA_ROLE, annotation.id)
                self.annotation_table.setItem(row, column, cell)
            if annotation.id == selected_id:
                self.annotation_table.selectRow(row)
        del blocker
        if selected_id is not None:
            self._populate_editor(session.get_annotation(selected_id))
        else:
            self.annotation_details.clear()
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
        self.label_edit.clear()
        self.concept_combo.clear()
        self.geometry_combo.clear()
        self.pinned_libraries.clear()
        self._waveform_generation += 1
        self._waveform_recording_id = None
        self.waveform.clear()
        self._player.stop()
        self._pending_seek = None
        self._playback_failure = ""
        self._player.setSource(QtCore.QUrl())
        self.playback_time.setText("0.00 / 0.00 s")

    @QtCore.Slot(bool)
    def _busy_changed(self, busy: bool) -> None:
        self._busy = busy
        self._update_actions()
        if busy:
            self.statusBar().showMessage("Working…")
        elif self._exit_requested:
            QtCore.QTimer.singleShot(0, self.close)

    @QtCore.Slot(str)
    def _show_error(self, message: str, *, retry: bool = False) -> None:
        self.error_banner.setText(message)
        self.error_retry_button.setVisible(retry)
        self.error_panel.show()
        self.statusBar().showMessage(message, 10_000)

    @QtCore.Slot(str)
    def _show_notice(self, message: str) -> None:
        self.error_panel.hide()
        self.statusBar().showMessage(message, 5_000)

    def _update_actions(self) -> None:
        session = self.controller.session
        has_session = session is not None
        recording_selected = self.recordings_list.currentItem() is not None
        library = self._selected_library()
        editable = has_session and not self._busy
        self.import_action.setEnabled(not self._busy)
        self.retry_saves_action.setEnabled(not self._busy)
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
        self.delete_annotation_button.setEnabled(bool(editable and has_selection))
        self.waveform.set_editable(editable)
        self.new_annotation_button.setEnabled(editable)
        self.concept_combo.setEnabled(editable and not has_selection)
        self.concept_search.setEnabled(editable and not has_selection)
        self.category_filter.setEnabled(editable and not has_selection)
        self.geometry_combo.setEnabled(editable and not has_selection)
        self.geometry_combo.setToolTip(
            "Geometry is locked when editing. Create a new annotation for a different type."
        )
        supported = self.geometry_combo.currentData() in {
            "time_interval",
            "point",
            "time_frequency_box",
        }
        for widget in (
            self.start_sample,
            self.end_sample,
            self.note_edit,
            self.label_edit,
            self.minimum_frequency,
            self.maximum_frequency,
        ):
            widget.setEnabled(bool(editable and supported))
        self.end_sample.setEnabled(
            bool(editable and supported and self.geometry_combo.currentData() != "point")
        )
        self.create_annotation_button.setVisible(not has_selection)
        self.update_annotation_button.setVisible(has_selection)
        self.delete_annotation_button.setVisible(has_selection)
        self.create_annotation_button.setEnabled(
            bool(
                editable
                and has_concept
                and supported
                and (self.waveform.selection or self.geometry_combo.currentData() == "point")
            )
        )
        self.update_annotation_button.setEnabled(bool(editable and has_selection and supported))
        self.editor_mode.setText(
            "Edit selected: geometry is locked. Use New annotation for a different type."
            if has_selection
            else "New annotation: select an interval on the audio view first."
        )
        if has_selection and not supported:
            self.editor_mode.setText(
                "Polygon editing is unavailable. Existing content is preserved."
            )
        self.use_core_button.setVisible(has_session and not self.controller.core_is_pinned)
        self.use_core_button.setEnabled(editable and self._library_status == "ready")
        if self._library_status == "loading":
            prerequisite = "Loading published libraries..."
        elif self._library_status == "failed":
            prerequisite = "Library retrieval failed. Use Refresh Libraries to retry."
        elif not any(v.latest_version_id for v in self.controller.libraries):
            prerequisite = "No published libraries available."
        elif not has_session:
            prerequisite = "Import or open a recording to annotate."
        elif session is not None and not session.pinned_libraries:
            prerequisite = "No pinned libraries. Choose Use core library, then Save Revision."
        elif not has_concept:
            prerequisite = "No available concepts in the pinned libraries."
        else:
            prerequisite = self.concept_combo.currentText()
        self.concept_status.setText(prerequisite)
        self._update_playback()

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
            "Audio files (*.wav *.mp3);;PCM WAV audio (*.wav);;MP3 audio (*.mp3);;All files (*)",
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
                if geometry_type == "time_frequency_polygon":
                    continue
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
        self.editor_form.setRowVisible(self.minimum_frequency, has_frequency)
        self.editor_form.setRowVisible(self.maximum_frequency, has_frequency)

    def _current_concept_choice(self) -> ConceptChoice | None:
        value = self.concept_combo.currentData()
        return value if isinstance(value, ConceptChoice) else None

    def _current_concept_reference(self) -> object | None:
        choice = self._current_concept_choice()
        return choice.reference if choice is not None else None

    @QtCore.Slot()
    def _create_annotation(self) -> None:
        if self.waveform.selection is None and self.geometry_combo.currentData() != "point":
            self._show_error("Select an interval on the audio view before creating an annotation")
            return
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
            label=self.label_edit.text().strip() or None,
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
            label=self.label_edit.text().strip() or None,
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
        self._refresh_concepts()
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
        if self.geometry_combo.findData(geometry.type) < 0:
            self.geometry_combo.addItem(geometry.type.replace("_", " "), geometry.type)
            self.geometry_combo.setCurrentIndex(self.geometry_combo.count() - 1)
        self.start_sample.setValue(geometry.start_sample)
        default_end = min(geometry.start_sample + 1, 2_147_483_647)
        self.end_sample.setValue(geometry.end_sample or default_end)
        if isinstance(geometry, (TimeFrequencyBoxGeometry, TimeFrequencyPolygonGeometry)):
            self.minimum_frequency.setValue(geometry.min_frequency_hz)
            self.maximum_frequency.setValue(geometry.max_frequency_hz)
        self.label_edit.setText(annotation.label or "")
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
        if self.save_dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        self.controller.save(
            author=self.author_edit.text().strip() or None,
            message=self.save_message_edit.text().strip() or None,
        )

    def _set_audio_source(self, path: Path) -> None:
        expected = QtCore.QUrl.fromLocalFile(str(path))
        if self._player.source() != expected:
            self._playback_failure = ""
            self._pending_seek = None
            self._player.stop()
            self._player.setSource(expected)
            self._update_playback()

    @QtCore.Slot()
    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @QtCore.Slot()
    def _stop_playback(self) -> None:
        self._pending_seek = None
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
        self._update_playback()
        self.play_pause_button.setText(
            "Pause" if state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState else "Play"
        )

    @QtCore.Slot(object, str)
    def _playback_error(self, _error: object, message: str) -> None:
        if message:
            self._playback_failure = f"Audio playback failed: {message}"
            self._update_playback()

    def _libraries_loading(self) -> None:
        self._library_status = "loading"
        self.libraries_state.setText("Loading...")
        self._update_actions()

    def _new_annotation(self) -> None:
        self._selected_annotation_id = None
        with QtCore.QSignalBlocker(self.annotation_table):
            self.annotation_table.clearSelection()
            self.annotation_table.setCurrentCell(-1, -1)
        self.waveform.set_selected_annotation(None)
        self.annotation_details.clear()
        self.label_edit.clear()
        self.note_edit.clear()
        self._refresh_concepts()
        if self.waveform.selection:
            self.start_sample.setValue(self.waveform.selection[0])
            self.end_sample.setValue(self.waveform.selection[1])
        self._update_actions()

    def _annotation_created(self, annotation_id: object) -> None:
        if isinstance(annotation_id, UUID):
            self._selected_annotation_id = annotation_id

    def _selection_changed(self, selection: object) -> None:
        if isinstance(selection, tuple):
            start, end = selection
            session = self.controller.session
            rate = session.audio.asset.sample_rate_hz if session else 1
            self.selection_readout.setText(
                f"Selection: {start / rate:.4f} - {end / rate:.4f} s | "
                f"duration {(end - start) / rate:.4f} s | samples {start}-{end}"
            )
            self._new_annotation()
            self.start_sample.setValue(start)
            self.end_sample.setValue(end)
        else:
            self.selection_readout.setText("Drag across the audio view to select an interval.")
        self._update_actions()

    def _boundary_readout_changed(self) -> None:
        session = self.controller.session
        if session:
            rate = session.audio.asset.sample_rate_hz
            start, end = self.start_sample.value(), self.end_sample.value()
            self.boundary_readout.setText(
                f"{start / rate:.4f} - {end / rate:.4f} s | {(end - start) / rate:.4f} s duration"
            )

    def _load_detail(self) -> None:
        session = self.controller.session
        if (
            self._shutdown
            or session is None
            or self._detail_pending
            or self.waveform.show_spectrogram
        ):
            return
        viewport = self.waveform.viewport
        generation = self._waveform_generation
        path = session.audio.local_path
        if viewport == (0, session.audio.asset.frame_count):
            return
        self._detail_pending = True

        def finished() -> None:
            self._detail_pending = False
            if not self._shutdown and (
                generation != self._waveform_generation or viewport != self.waveform.viewport
            ):
                self._detail_timer.start()

        def success(envelope: WaveformEnvelope) -> None:
            if generation == self._waveform_generation and viewport == self.waveform.viewport:
                self.waveform.set_detail(envelope)
            finished()

        def failure(_exc: BaseException) -> None:
            if generation == self._waveform_generation:
                self._show_error(
                    "Could not load waveform detail. Fit recording or zoom again to retry."
                )
            finished()

        self._waveform_runner.submit(
            lambda: load_waveform_detail(path, *viewport), success, failure
        )

    def _audio_view_changed(self, index: int) -> None:
        self.waveform.set_spectrogram_mode(index == 1)
        self._detail_timer.start()

    def _load_spectrogram(self) -> None:
        session = self.controller.session
        if (
            self._shutdown
            or session is None
            or self._spectrogram_pending
            or not self.waveform.show_spectrogram
            or not self.waveform.audio_ready
        ):
            return
        viewport = self.waveform.viewport
        preview = self.waveform.spectrogram
        if preview is not None and (preview.start_sample, preview.end_sample) == viewport:
            self.waveform.set_spectrogram_message("")
            return
        generation = self._waveform_generation
        path = session.audio.local_path
        self._spectrogram_pending = True
        self.waveform.set_spectrogram_message("Preparing spectrogram...")

        def current() -> bool:
            return (
                not self._shutdown
                and generation == self._waveform_generation
                and viewport == self.waveform.viewport
            )

        def finished() -> None:
            self._spectrogram_pending = False
            if not self._shutdown and not current():
                self._detail_timer.start()

        def success(result: SpectrogramPreview) -> None:
            if current():
                self.waveform.set_spectrogram(result)
            finished()

        def failure(_exc: BaseException) -> None:
            if current():
                self.waveform.set_spectrogram_message(
                    "Spectrogram unavailable. Switch views or zoom to retry."
                )
            finished()

        self._waveform_runner.submit(lambda: load_spectrogram(path, *viewport), success, failure)

    def _seek_sample(self, sample: int) -> None:
        session = self.controller.session
        if session is not None and self._selected_annotation_id is None:
            self.start_sample.setValue(min(sample, session.audio.asset.frame_count - 1))
        if session is not None and self._player.isSeekable():
            self._player.setPosition(round(sample * 1000 / session.audio.asset.sample_rate_hz))
        elif session is not None:
            self._pending_seek = sample
            self._update_playback()

    def _update_playback(self) -> None:
        player = self._player
        status = player.mediaStatus()
        ready = status in {
            QtMultimedia.QMediaPlayer.MediaStatus.LoadedMedia,
            QtMultimedia.QMediaPlayer.MediaStatus.BufferedMedia,
            QtMultimedia.QMediaPlayer.MediaStatus.BufferingMedia,
            QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia,
        }
        if ready and player.isSeekable() and self._pending_seek is not None:
            pending, self._pending_seek = self._pending_seek, None
            self._seek_sample(pending)
        device_available = bool(QtMultimedia.QMediaDevices.audioOutputs())
        if self._playback_failure:
            message = self._playback_failure
        elif not device_available:
            message = "No audio output device available. Connect or enable a Windows output device."
        elif player.source().isEmpty():
            message = "No audio loaded"
        elif status == QtMultimedia.QMediaPlayer.MediaStatus.InvalidMedia:
            message = "Audio could not be loaded: " + player.errorString()
        elif not ready:
            message = "Loading audio... " + status.name
        else:
            state = player.playbackState().name.removesuffix("State")
            message = f"Audio ready | {state} | " + (
                "seekable" if player.isSeekable() else "not seekable"
            )
        self.playback_status.setText(message)
        self.play_pause_button.setEnabled(
            bool(ready and device_available and not self._playback_failure)
        )
        self.stop_button.setEnabled(not player.source().isEmpty())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._shutdown:
            event.accept()
            return
        if self._busy:
            self._exit_requested = True
            self.statusBar().showMessage(
                "Waiting for the current operation before saving and exiting…"
            )
            event.ignore()
            return
        session = self.controller.session
        if session is not None and session.sync_state is SyncState.CONFLICT:
            self._exit_requested = False
            self._show_error(
                "Resolve the save conflict before exiting. Recovery files are preserved."
            )
            event.ignore()
            return
        if session is not None and session.dirty:
            event.ignore()
            self._exit_requested = False
            self.save_dialog.setWindowTitle("Save before exiting")
            accepted = self.save_dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
            self.save_dialog.setWindowTitle("Save revision")
            if accepted:
                self._exit_requested = True
                self.controller.save(
                    author=self.author_edit.text().strip() or None,
                    message=self.save_message_edit.text().strip() or None,
                )
            return
        if session is not None and session.sync_state is SyncState.PENDING:
            event.ignore()
            self._exit_requested = True
            self.controller.retry_recovery()
            return
        if not self.controller.close_session():
            self._exit_requested = False
            event.ignore()
            return
        self.shutdown()
        event.accept()

    def _exit_save_finished(self, saved: bool) -> None:
        if self._exit_requested and not saved:
            self._exit_requested = False
            session = self.controller.session
            if session is not None and session.sync_state is SyncState.PENDING:
                self._show_error(
                    "Changes are saved locally, but the database save did not complete. "
                    "Use Retry Pending Saves, then close again.",
                    retry=True,
                )

    def _exit_recovery_finished(self, recovered: bool) -> None:
        if not recovered:
            self._exit_requested = False

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._detail_timer.stop()
        self._player.stop()
        self.controller.shutdown()
        self._waveform_runner.shutdown()
        shutdown = getattr(self._task_runner, "shutdown", None)
        if callable(shutdown):
            shutdown()
