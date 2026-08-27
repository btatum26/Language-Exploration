from __future__ import annotations

from uuid import UUID

from PySide6 import QtCore, QtGui, QtWidgets

from spectrogram_playground.analysis.cache import AnalysisCache
from spectrogram_playground.audio.decoding import AudioDecodeError, load_track, track_from_samples
from spectrogram_playground.audio.editing import (
    AudioClip,
    copy_region,
    delete_region,
    move_region,
    paste_clip,
)
from spectrogram_playground.audio.engine import AudioEngine
from spectrogram_playground.audio.recording import Recorder, RecordingError
from spectrogram_playground.exporting import export_project_json
from spectrogram_playground.fixtures import sine
from spectrogram_playground.model.project import Project
from spectrogram_playground.model.settings import VisualizationTab
from spectrogram_playground.model.track import Track
from spectrogram_playground.model.transport import TransportState

from .analysis_settings import AnalysisSettingsPanel
from .export_worker import AcousticCsvExportTask
from .fourier_view import FourierView
from .pattern_guide import PatternGuide
from .recording_indicator import RecordingIndicator
from .spectrogram_view import SpectrogramWorkspace
from .timeline_ruler import TimelineRuler
from .transport_toolbar import TransportToolbar
from .waveform_view import WaveformWorkspace


class MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        project: Project | None = None,
        engine: AudioEngine | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project or Project()
        self.engine = engine or AudioEngine(self.project)
        self.cache = AnalysisCache()
        self.audio_clipboard: AudioClip | None = None
        self._export_tasks: list[AcousticCsvExportTask] = []
        self._export_pool = QtCore.QThreadPool(self)
        self._export_pool.setMaxThreadCount(1)
        self.recorder = Recorder(self.project.playback_rate)
        self.setWindowTitle("Spectrogram Playground")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)
        self._build_menu()
        self.toolbar = TransportToolbar(self)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self._connect_toolbar()

        central = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(central)
        center_layout.setContentsMargins(3, 3, 3, 3)
        center_layout.setSpacing(2)
        self.recording_indicator = RecordingIndicator()
        self.recording_indicator.stop_requested.connect(self.toggle_recording)
        center_layout.addWidget(self.recording_indicator)
        self.ruler = TimelineRuler(self.project)
        center_layout.addWidget(self.ruler)
        self.tabs = QtWidgets.QTabWidget()
        self._lane_heights: dict[UUID, int] = {}
        self.waveform = WaveformWorkspace(self.project, self.cache, lane_heights=self._lane_heights)
        self.spectrogram = SpectrogramWorkspace(
            self.project, self.cache, lane_heights=self._lane_heights
        )
        self.waveform.lane_height_changed.connect(self.spectrogram.set_lane_height)
        self.spectrogram.lane_height_changed.connect(self.waveform.set_lane_height)
        self.fourier = FourierView(self.project, self.cache)
        self.tabs.addTab(self.waveform, "Waveform")
        self.tabs.addTab(self.spectrogram, "Spectrogram")
        self.tabs.addTab(self.fourier, "Fourier Transform")
        center_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self._connect_workspace(self.waveform)
        self._connect_workspace(self.spectrogram)
        self._connect_plot(self.ruler)
        self.waveform.analysis_failed.connect(self._show_error)
        self.spectrogram.analysis_failed.connect(self._show_error)
        self.fourier.analysis_failed.connect(self._show_error)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._build_docks()

        self.device_status = QtWidgets.QLabel("Audio device: idle")
        self.selection_status = QtWidgets.QLabel("No selection")
        self.statusBar().addWidget(self.selection_status, 1)
        self.statusBar().addPermanentWidget(
            QtWidgets.QLabel(
                f"Playback {self.project.playback_rate / 1000:g} kHz Â· "
                f"Analysis {self.project.settings.analysis_rate / 1000:g} kHz"
            )
        )
        self.statusBar().addPermanentWidget(self.device_status)
        self.timer = QtCore.QTimer(self, interval=25)
        self.timer.timeout.connect(self._refresh_playhead)
        self.timer.start()
        self.rebuild_views()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("Add trackâ€¦", self.choose_tracks, QtGui.QKeySequence.StandardKey.Open)
        file_menu.addAction("Export current view as PNGâ€¦", self.export_png)
        file_menu.addAction("Export F0, F1-F3, and RMS as CSVâ€¦", self.export_csv)
        file_menu.addAction("Export project settings as JSONâ€¦", self.export_json)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close, QtGui.QKeySequence.StandardKey.Quit)
        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(
            "Copy selection", self.copy_selection, QtGui.QKeySequence.StandardKey.Copy
        )
        edit_menu.addAction("Cut selection", self.cut_selection, QtGui.QKeySequence.StandardKey.Cut)
        edit_menu.addAction(
            "Paste at playhead", self.paste_selection, QtGui.QKeySequence.StandardKey.Paste
        )
        edit_menu.addAction(
            "Delete selection",
            self.delete_selection,
            QtGui.QKeySequence(QtCore.Qt.Key.Key_Delete),
        )
        edit_menu.addSeparator()
        edit_menu.addAction(
            "Move selection to playhead",
            self.move_selection_to_playhead,
            QtGui.QKeySequence("Ctrl+M"),
        )
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction("Analysis settings", lambda: self.settings_dock.setVisible(True))
        view_menu.addAction("Pattern guide", lambda: self.guide_dock.setVisible(True))
        track_menu = self.menuBar().addMenu("&Track")
        track_menu.addAction("Trim active track start to playhead", self.trim_start_to_playhead)
        track_menu.addAction("Trim active track end to playhead", self.trim_end_to_playhead)
        track_menu.addAction("Trim active track to selection", self.trim_to_selection)
        track_menu.addSeparator()
        track_menu.addAction("Reset active track trim", self.reset_active_trim)
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction("Add generated 440 Hz fixture", self.add_demo_track)
        audio_menu = self.menuBar().addMenu("&Audio")
        audio_menu.addAction("Select input deviceâ€¦", self.choose_input_device)

    def _build_docks(self) -> None:
        self.settings_dock = QtWidgets.QDockWidget("Analysis settings", self)
        self.settings_panel = AnalysisSettingsPanel(self.project)
        self.settings_panel.settings_changed.connect(self.analysis_settings_changed)
        self.settings_dock.setWidget(self.settings_panel)
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.settings_dock)
        self.guide_dock = QtWidgets.QDockWidget("Educational pattern guide", self)
        self.guide_dock.setWidget(PatternGuide())
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, self.guide_dock)
        self.tabifyDockWidget(self.settings_dock, self.guide_dock)
        self.settings_dock.raise_()

    def _connect_toolbar(self) -> None:
        self.toolbar.add_requested.connect(self.choose_tracks)
        self.toolbar.record_requested.connect(self.toggle_recording)
        self.toolbar.play_requested.connect(self.toggle_playback)
        self.toolbar.stop_requested.connect(self.stop)
        self.toolbar.beginning_requested.connect(lambda: self.seek(0))
        self.toolbar.end_requested.connect(lambda: self.seek(self.project.duration))
        self.toolbar.loop_changed.connect(self._set_loop)
        self.toolbar.follow_changed.connect(self._set_follow)
        self.toolbar.zoom_in_requested.connect(lambda: self.zoom(0.8))
        self.toolbar.zoom_out_requested.connect(lambda: self.zoom(1.25))
        self.toolbar.zoom_selection_requested.connect(self.zoom_to_selection)
        self.toolbar.fit_requested.connect(self.fit_project)
        self.toolbar.reset_requested.connect(self.reset_zoom)

    def _connect_plot(self, plot: object) -> None:
        plot.seek_requested.connect(self.seek)  # type: ignore[attr-defined]
        plot.selection_requested.connect(self.set_selection)  # type: ignore[attr-defined]
        plot.pan_requested.connect(self.pan)  # type: ignore[attr-defined]
        plot.zoom_requested.connect(self.zoom)  # type: ignore[attr-defined]

    def _connect_workspace(self, workspace: object) -> None:
        workspace.seek_requested.connect(self.seek)  # type: ignore[attr-defined]
        workspace.selection_requested.connect(self.set_track_selection)  # type: ignore[attr-defined]
        workspace.pan_requested.connect(self.pan)  # type: ignore[attr-defined]
        workspace.zoom_requested.connect(self.zoom)  # type: ignore[attr-defined]
        workspace.changed.connect(self.rebuild_views)
        workspace.remove_requested.connect(self.remove_track)
        workspace.move_requested.connect(self.move_track)
        workspace.activated.connect(self.activate_track)

    @QtCore.Slot()
    def choose_tracks(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Add audio tracks",
            "",
            "Audio (*.wav *.flac *.ogg *.mp3);;All files (*)",
        )
        for path in paths:
            try:
                self.add_track(
                    load_track(
                        path,
                        project_rate=self.project.playback_rate,
                        analysis_rate=self.project.settings.analysis_rate,
                        color_index=len(self.project.tracks),
                    )
                )
            except AudioDecodeError as exc:
                self._show_error(str(exc))

    def add_track(self, track: object) -> None:
        self.project.add_track(track)  # type: ignore[arg-type]
        self.rebuild_views()
        self.statusBar().showMessage(f"Added {track.name}", 3000)

    @QtCore.Slot()
    def add_demo_track(self) -> None:
        index = len(self.project.tracks)
        self.add_track(
            track_from_samples(
                sine(duration=2, rate=16_000),
                16_000,
                name=f"440 Hz fixture {index + 1}",
                project_rate=self.project.playback_rate,
                analysis_rate=self.project.settings.analysis_rate,
                color_index=index,
            )
        )

    @QtCore.Slot()
    def toggle_playback(self) -> None:
        try:
            if self.project.transport.state is TransportState.PLAYING:
                self.engine.pause()
                self.device_status.setText("Audio device: paused")
            else:
                self.engine.play()
                self.device_status.setText("Audio device: playing")
        except Exception as exc:
            self.engine.pause()
            self._show_error(f"Could not start audio output: {exc}")

    @QtCore.Slot()
    def stop(self) -> None:
        self.engine.stop()
        self.device_status.setText("Audio device: stopped")
        self._refresh_playhead()

    @QtCore.Slot()
    def toggle_recording(self) -> None:
        try:
            if self.recorder.active:
                samples = self.recorder.stop()
                self.toolbar.record_action.setText("Record")
                self.toolbar.set_recording_feedback(False)
                self.recording_indicator.setVisible(False)
                self.device_status.setText("Audio device: recording stopped")
                self.add_track(
                    track_from_samples(
                        samples,
                        self.recorder.sample_rate,
                        name=f"Recording {len(self.project.tracks) + 1}",
                        project_rate=self.project.playback_rate,
                        analysis_rate=self.project.settings.analysis_rate,
                        origin="microphone",
                        color_index=len(self.project.tracks),
                    )
                )
            else:
                if self.recorder.device is None and not self.choose_input_device():
                    return
                self.engine.pause()
                self.engine.backend.stop()
                self.recorder.start()
                self.toolbar.record_action.setText("Stop recording")
                self.recording_indicator.setVisible(True)
                self.recording_indicator.update_recording(0.0, 0.0, self.recorder.device_name)
                self.device_status.setText("Audio device: recording")
        except RecordingError as exc:
            self.toolbar.record_action.setText("Record")
            self.toolbar.set_recording_feedback(False)
            self.recording_indicator.setVisible(False)
            self._show_error(str(exc))

    @QtCore.Slot()
    def choose_input_device(self) -> bool:
        if self.recorder.active:
            self._show_error("Stop recording before changing the input device")
            return False
        try:
            devices = self.recorder.available_devices()
        except RecordingError as exc:
            self._show_error(str(exc))
            return False
        if not devices:
            self._show_error("No microphone input devices were found")
            return False
        labels = [device.label for device in devices]
        current = next(
            (index for index, device in enumerate(devices) if device.index == self.recorder.device),
            0,
        )
        label, accepted = QtWidgets.QInputDialog.getItem(
            self,
            "Select microphone",
            "Input device",
            labels,
            current,
            False,
        )
        if not accepted:
            return False
        self.recorder.select_device(devices[labels.index(label)])
        self.statusBar().showMessage(f"Input device: {self.recorder.device_name}", 5000)
        return True

    @QtCore.Slot(float)
    def seek(self, seconds: float) -> None:
        self.engine.seek_seconds(min(max(0.0, seconds), self.project.duration))
        self._refresh_playhead()

    @QtCore.Slot(float, float)
    def set_selection(self, first: float, second: float) -> None:
        if self.project.active_track_id is None:
            return
        track = self.project.track_by_id(self.project.active_track_id)
        self.project.selection.set(first, second, track.duration)
        self._refresh_timeline()
        if self.tabs.currentIndex() == 2:
            self.fourier.refresh()

    @QtCore.Slot(object, float, float)
    def set_track_selection(self, track_id: UUID, first: float, second: float) -> None:
        self.project.active_track_id = track_id
        track = self.project.track_by_id(track_id)
        self.project.selection_for(track_id).set(first, second, track.duration)
        self.waveform.set_active_track(track_id)
        self.spectrogram.set_active_track(track_id)
        self._refresh_timeline()
        if self.tabs.currentIndex() == 2:
            self.fourier.refresh()

    @QtCore.Slot(float)
    def pan(self, delta: float) -> None:
        self.project.viewport.pan(delta, self.project.duration)
        self._refresh_timeline()

    @QtCore.Slot(float, float)
    def zoom(self, factor: float, anchor: float | None = None) -> None:
        anchor = (
            self.project.transport.frame_position / self.project.playback_rate
            if anchor is None
            else anchor
        )
        self.project.viewport.zoom(factor, anchor, self.project.duration)
        self._refresh_timeline()

    def zoom_to_selection(self) -> None:
        if self.project.selection.active:
            self.project.viewport.set(
                self.project.selection.start, self.project.selection.end, self.project.duration
            )
            self._refresh_timeline()

    def fit_project(self) -> None:
        self.project.viewport.fit(self.project.duration)
        self._refresh_timeline()

    def reset_zoom(self) -> None:
        self.project.viewport.set(
            0, min(10.0, max(0.01, self.project.duration)), self.project.duration
        )
        self._refresh_timeline()

    @QtCore.Slot(object)
    def activate_track(self, track_id: UUID) -> None:
        self.project.active_track_id = track_id
        self.rebuild_views()

    @QtCore.Slot(object, int)
    def move_track(self, track_id: UUID, delta: int) -> None:
        self.project.move_track(track_id, delta)
        self.rebuild_views()

    @QtCore.Slot(object)
    def remove_track(self, track_id: UUID) -> None:
        self.engine.stop()
        self.project.remove_track(track_id)
        self.cache.invalidate_track(track_id)
        self.rebuild_views()

    def _active_track_for_edit(self) -> Track | None:
        if self.project.active_track_id is None:
            self._show_error("Select an active track before editing")
            return None
        return self.project.track_by_id(self.project.active_track_id)

    def _selected_audio(self) -> tuple[Track, float, float] | None:
        track = self._active_track_for_edit()
        if track is None:
            return None
        if not self.project.selection.active:
            self._show_error("Ctrl+drag to select audio first")
            return None
        return track, self.project.selection.start, self.project.selection.end

    @QtCore.Slot()
    def copy_selection(self) -> bool:
        selected = self._selected_audio()
        if selected is None:
            return False
        track, start, end = selected
        try:
            self.audio_clipboard = copy_region(track, start, end)
        except ValueError as exc:
            self._show_error(str(exc))
            return False
        self.statusBar().showMessage(
            f"Copied {self.audio_clipboard.duration:.3f} seconds from {track.name}", 4000
        )
        return True

    @QtCore.Slot()
    def cut_selection(self) -> None:
        if self.copy_selection():
            self.delete_selection()

    @QtCore.Slot()
    def delete_selection(self) -> None:
        selected = self._selected_audio()
        if selected is None:
            return
        track, start, end = selected
        self.engine.stop()
        try:
            with self.engine.mixer.lock:
                target = delete_region(track, start, end)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._finish_audio_edit(track, target, None)
        self.statusBar().showMessage(f"Deleted selection from {track.name}", 4000)

    @QtCore.Slot()
    def paste_selection(self) -> None:
        track = self._active_track_for_edit()
        if track is None:
            return
        if self.audio_clipboard is None:
            self._show_error("Copy a selection before pasting")
            return
        target = self.engine.current_time
        self.engine.stop()
        with self.engine.mixer.lock:
            inserted = paste_clip(track, target, self.audio_clipboard)
        self._finish_audio_edit(track, inserted[1], inserted)
        self.statusBar().showMessage(f"Pasted selection into {track.name}", 4000)

    @QtCore.Slot()
    def move_selection_to_playhead(self) -> None:
        selected = self._selected_audio()
        if selected is None:
            return
        track, start, end = selected
        target = self.engine.current_time
        self.engine.stop()
        try:
            with self.engine.mixer.lock:
                moved = move_region(track, start, end, target)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self._finish_audio_edit(track, moved[1], moved)
        self.statusBar().showMessage(f"Moved selection in {track.name}", 4000)

    def _finish_audio_edit(
        self, track: Track, playhead: float, selection: tuple[float, float] | None
    ) -> None:
        self.cache.invalidate_track(track.id)
        if selection is None:
            self.project.selection.clear()
        else:
            self.project.selection.set(*selection, self.project.duration)
        self.project.viewport.set(
            self.project.viewport.start,
            self.project.viewport.end,
            self.project.duration,
        )
        self.engine.seek_seconds(playhead)
        self.rebuild_views()

    def _active_track_for_trim(self) -> Track | None:
        if self.project.active_track_id is None:
            self._show_error("Select an active track before trimming")
            return None
        return self.project.track_by_id(self.project.active_track_id)

    @QtCore.Slot()
    def trim_start_to_playhead(self) -> None:
        track = self._active_track_for_trim()
        if track is not None:
            self._apply_trim(track, self.engine.current_time, track.duration)

    @QtCore.Slot()
    def trim_end_to_playhead(self) -> None:
        track = self._active_track_for_trim()
        if track is not None:
            self._apply_trim(track, 0.0, self.engine.current_time)

    @QtCore.Slot()
    def trim_to_selection(self) -> None:
        track = self._active_track_for_trim()
        if track is None:
            return
        if not self.project.selection.active:
            self._show_error("Create a time selection before trimming")
            return
        self._apply_trim(track, self.project.selection.start, self.project.selection.end)

    def _apply_trim(self, track: Track, start: float, end: float) -> None:
        self.engine.stop()
        try:
            track.trim(start, end)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.cache.invalidate_track(track.id)
        self.project.selection.clear()
        self.project.transport.seek(0, self.project.total_frames)
        self.project.viewport.fit(self.project.duration)
        self.rebuild_views()
        self.statusBar().showMessage(f"Trimmed {track.name} to {track.duration:.3f} seconds", 5000)

    @QtCore.Slot()
    def reset_active_trim(self) -> None:
        track = self._active_track_for_trim()
        if track is None:
            return
        self.engine.stop()
        track.reset_trim()
        self.cache.invalidate_track(track.id)
        self.project.selection.clear()
        self.project.transport.seek(0, self.project.total_frames)
        self.project.viewport.fit(self.project.duration)
        self.rebuild_views()
        self.statusBar().showMessage(f"Reset trim for {track.name}", 5000)

    def rebuild_views(self) -> None:
        self.waveform.rebuild()
        self.spectrogram.rebuild()
        if self.tabs.currentIndex() == 2:
            self.fourier.refresh()
        self._refresh_timeline()

    def analysis_settings_changed(self) -> None:
        self.spectrogram.rebuild()
        if self.tabs.currentIndex() == 2:
            self.fourier.refresh()

    @QtCore.Slot(int)
    def _tab_changed(self, index: int) -> None:
        self.project.active_tab = (
            VisualizationTab.WAVEFORM,
            VisualizationTab.SPECTROGRAM,
            VisualizationTab.FOURIER,
        )[index]
        if index == 2:
            self.fourier.refresh()
        self._refresh_timeline()

    def _refresh_playhead(self) -> None:
        position = self.engine.current_time
        self.toolbar.set_time(position)
        if self.recorder.active:
            self.toolbar.set_recording_feedback(
                True, self.recorder.duration, self.recorder.peak_level
            )
            self.recording_indicator.setVisible(True)
            self.recording_indicator.update_recording(
                self.recorder.duration,
                self.recorder.peak_level,
                self.recorder.device_name,
            )
            detail = f"Audio device: recording Â· {self.recorder.device_name}"
            if self.recorder.last_error:
                detail += f" Â· {self.recorder.last_error}"
            self.device_status.setText(detail)
            self._update_visible_playheads(position)
            return
        backend_error = getattr(self.engine.backend, "last_error", None)
        if backend_error:
            self.device_status.setText(f"Audio warning: {backend_error}")
        viewport_changed = False
        if self.project.transport.follow_playhead and self.project.transport.is_playing:
            if position > self.project.viewport.end or position < self.project.viewport.start:
                width = self.project.viewport.width
                self.project.viewport.set(
                    position - width * 0.15, position + width * 0.85, self.project.duration
                )
                viewport_changed = True
        if viewport_changed:
            self._refresh_timeline(position)
        else:
            self._update_visible_playheads(position)
        if self.project.transport.state is TransportState.STOPPED:
            self.device_status.setText("Audio device: stopped")

    def _update_visible_playheads(self, position: float) -> None:
        self.ruler.update_playhead(position)
        if self.tabs.currentIndex() == 0:
            self.waveform.update_playhead(position)
        elif self.tabs.currentIndex() == 1:
            self.spectrogram.update_playhead(position)

    def _refresh_timeline(self, position: float | None = None) -> None:
        position = self.engine.current_time if position is None else position
        self.ruler.refresh(position)
        if self.tabs.currentIndex() == 0:
            self.waveform.update_timeline(position)
        elif self.tabs.currentIndex() == 1:
            self.spectrogram.update_timeline(position)
        if self.project.selection.active:
            track = self.project.track_by_id(self.project.active_track_id)
            self.selection_status.setText(
                f"{track.name}: {self.project.selection.start:.3f}â€“"
                f"{self.project.selection.end:.3f} s "
                f"({self.project.selection.duration:.3f} s)"
            )
        else:
            self.selection_status.setText("No selection")

    def _set_loop(self, value: bool) -> None:
        self.project.transport.loop_selection = value

    def _set_follow(self, value: bool) -> None:
        self.project.transport.follow_playhead = value

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.warning(self, "Spectrogram Playground", message)

    def export_png(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export view", "view.png", "PNG (*.png)"
        )
        if not path:
            return
        widget = (self.waveform, self.spectrogram, self.fourier)[self.tabs.currentIndex()]
        if not widget.grab().save(path, "PNG"):
            self._show_error("Could not write the PNG file")

    def export_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export F0, F1-F3, and RMS", "acoustics.csv", "CSV (*.csv)"
        )
        if path:
            task = AcousticCsvExportTask(self.project, self.cache, path)
            task.signals.completed.connect(self._export_finished)
            task.signals.failed.connect(self._export_failed)
            self._export_tasks.append(task)
            self.statusBar().showMessage("Calculating F0/F1-F3/RMS and exporting CSV...")
            self._export_pool.start(task)

    @QtCore.Slot(str)
    def _export_finished(self, path: str) -> None:
        self._discard_export_task()
        self.statusBar().showMessage(f"Exported {path}", 5000)

    @QtCore.Slot(str)
    def _export_failed(self, message: str) -> None:
        self._discard_export_task()
        self._show_error(f"Could not export acoustic tracks: {message}")

    def _discard_export_task(self) -> None:
        source = self.sender()
        self._export_tasks = [task for task in self._export_tasks if task.signals is not source]

    def export_json(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export project settings", "project.json", "JSON (*.json)"
        )
        if path:
            export_project_json(self.project, path)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        focus = QtWidgets.QApplication.focusWidget()
        if isinstance(
            focus, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox, QtWidgets.QTextEdit)
        ):
            return super().keyPressEvent(event)
        key, modifiers = event.key(), event.modifiers()
        if key == QtCore.Qt.Key.Key_Space:
            self.toggle_playback()
        elif key == QtCore.Qt.Key.Key_Home:
            self.seek(0)
        elif key in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
            step = 0.5 if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier else 0.05
            direction = -1 if key == QtCore.Qt.Key.Key_Left else 1
            self.seek(self.engine.current_time + direction * step)
        elif key in (QtCore.Qt.Key.Key_Plus, QtCore.Qt.Key.Key_Equal):
            self.zoom(0.8)
        elif key == QtCore.Qt.Key.Key_Minus:
            self.zoom(1.25)
        elif key == QtCore.Qt.Key.Key_Escape:
            self.project.selection.clear()
            self._refresh_timeline()
        else:
            return super().keyPressEvent(event)
        event.accept()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.timer.stop()
        self.recorder.close()
        self.engine.close()
        self.waveform.wait_for_tasks()
        self.spectrogram.wait_for_tasks()
        self.fourier.wait_for_tasks()
        self._export_pool.waitForDone(5_000)
        self._export_tasks.clear()
        event.accept()
