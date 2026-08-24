from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6 import QtCore, QtWidgets
from src.audio.recording import InputDevice, Recorder, RecordingError

from alignment_workbench.services.models import IngestRequest
from alignment_workbench.services.tasks import TaskManager


class RecordingPanel(QtWidgets.QWidget):
    preview_audio = QtCore.Signal(object, str)
    save_requested = QtCore.Signal(object)
    create_speaker_requested = QtCore.Signal(str, str, str)
    error = QtCore.Signal(str)

    def __init__(self, tasks: TaskManager, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.tasks = tasks
        self.recorder = Recorder()
        self.take_path: Path | None = None
        self._build()
        self.level_timer = QtCore.QTimer(self)
        self.level_timer.setInterval(50)
        self.level_timer.timeout.connect(self._update_level)
        tasks.completed.connect(self._task_completed)
        tasks.failed.connect(self._task_failed)
        QtCore.QTimer.singleShot(0, self.refresh_devices)

    def _build(self) -> None:
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.addWidget(QtWidgets.QLabel("Speaker"), 0, 0)
        self.speaker = QtWidgets.QComboBox()
        self.speaker.setEditable(True)
        layout.addWidget(self.speaker, 0, 1)
        new_speaker = QtWidgets.QToolButton()
        new_speaker.setText("New…")
        new_speaker.clicked.connect(self.create_speaker)
        layout.addWidget(new_speaker, 0, 2)
        layout.addWidget(QtWidgets.QLabel("Language"), 0, 3)
        self.language = QtWidgets.QComboBox()
        self.language.setEditable(True)
        self.language.addItems(("it", "en"))
        layout.addWidget(self.language, 0, 4, 1, 2)
        layout.addWidget(QtWidgets.QLabel("Recording ID"), 1, 0)
        self.recording_id = QtWidgets.QLineEdit()
        layout.addWidget(self.recording_id, 1, 1)
        layout.addWidget(QtWidgets.QLabel("Exact transcript"), 1, 2)
        self.transcript = QtWidgets.QLineEdit()
        layout.addWidget(self.transcript, 1, 3, 1, 3)
        layout.addWidget(QtWidgets.QLabel("Input"), 2, 0)
        self.device = QtWidgets.QComboBox()
        layout.addWidget(self.device, 2, 1, 1, 3)
        self.level = QtWidgets.QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        layout.addWidget(self.level, 2, 4, 1, 2)
        controls = QtWidgets.QHBoxLayout()
        for label, callback in (
            ("Start", self.start),
            ("Stop", self.stop),
            ("Cancel", self.cancel),
            ("Import", self.import_audio),
            ("Preview", self.preview),
            ("Retake", self.retake),
            ("Save + align", self.save),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls, 3, 0, 1, 6)
        self.status = QtWidgets.QLabel("Ready")
        layout.addWidget(self.status, 4, 0, 1, 6)

    def set_speakers(self, speakers: tuple[dict[str, object], ...]) -> None:
        current = self.selected_speaker_id()
        self.speaker.clear()
        for item in speakers:
            identifier = str(item["id"])
            display = str(item.get("display_name") or identifier)
            self.speaker.addItem(f"{display} [{identifier}]", identifier)
        if current:
            index = self.speaker.findData(current)
            if index >= 0:
                self.speaker.setCurrentIndex(index)
            else:
                self.speaker.setCurrentText(current)

    def selected_speaker_id(self) -> str | None:
        value = self.speaker.currentData()
        return str(value) if value else self.speaker.currentText().strip() or None

    def create_speaker(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Create speaker")
        form = QtWidgets.QFormLayout(dialog)
        identifier = QtWidgets.QLineEdit()
        display_name = QtWidgets.QLineEdit()
        form.addRow("Speaker ID", identifier)
        form.addRow("Display name", display_name)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        speaker_id = identifier.text().strip()
        name = display_name.text().strip()
        if not speaker_id or not name:
            self.error.emit("Speaker ID and display name are required")
            return
        self.create_speaker_requested.emit(
            speaker_id, name, self.language.currentText().strip() or "it"
        )

    def refresh_devices(self) -> None:
        self.status.setText("Querying input devices…")
        self.tasks.submit(
            "input-devices",
            lambda _cancel, _progress: self.recorder.available_devices(),
            replace=True,
        )

    def _set_devices(self, devices: list[InputDevice]) -> None:
        self.device.clear()
        self.device.addItem("System default", None)
        for device in devices:
            self.device.addItem(device.label, device)
        self.status.setText("Ready")

    def start(self) -> None:
        device = self.device.currentData()
        try:
            if isinstance(device, InputDevice):
                self.recorder.select_device(device)
            self.recorder.start()
        except RecordingError as exc:
            self.error.emit(str(exc))
            return
        self.take_path = None
        self.level_timer.start()
        self.status.setText("Recording…")

    def stop(self) -> None:
        try:
            samples = self.recorder.stop()
        except RecordingError as exc:
            self.error.emit(str(exc))
            return
        self.level_timer.stop()
        directory = (
            Path(
                QtCore.QStandardPaths.writableLocation(
                    QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation
                )
            )
            / "takes"
        )
        destination = directory / f"take-{QtCore.QDateTime.currentMSecsSinceEpoch()}.wav"
        rate = self.recorder.sample_rate
        self.status.setText("Writing take…")

        def write_take(_cancel: object, _progress: object) -> tuple[Path, float]:
            directory.mkdir(parents=True, exist_ok=True)
            sf.write(destination, np.asarray(samples, dtype=np.float32), rate)
            return destination, len(samples) / rate

        self.tasks.submit("take-write", write_take, replace=True)

    @QtCore.Slot(str, object, object)
    def _task_completed(self, category: str, _request_id: object, result: object) -> None:
        if category == "input-devices":
            self._set_devices(result)  # type: ignore[arg-type]
        elif category == "take-write":
            self.take_path, duration = result  # type: ignore[misc]
            self.status.setText(f"Take ready · {duration:.2f}s")
            self.preview_audio.emit(self.take_path, "recording")

    @QtCore.Slot(str, object, str)
    def _task_failed(self, category: str, _request_id: object, message: str) -> None:
        if category in {"input-devices", "take-write"}:
            self.status.setText(message)
            if category == "take-write":
                self.error.emit(message)

    def cancel(self) -> None:
        if self.recorder.active:
            try:
                self.recorder.stop()
            except RecordingError:
                pass
        self.level_timer.stop()
        if self.take_path is not None and self.take_path.is_file():
            self.take_path.unlink(missing_ok=True)
        self.take_path = None
        self.status.setText("Cancelled; nothing was saved")

    def import_audio(self) -> None:
        value, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import audio",
            "",
            "Audio (*.wav *.flac *.ogg *.mp3);;All files (*)",
        )
        if value:
            self.take_path = Path(value)
            self.status.setText(f"Imported {self.take_path.name}; preview before saving")
            self.preview_audio.emit(self.take_path, "import")

    def preview(self) -> None:
        if self.take_path is not None:
            self.preview_audio.emit(self.take_path, "preview")

    def retake(self) -> None:
        self.cancel()
        self.start()

    def save(self) -> None:
        if self.take_path is None:
            self.error.emit("Record or import audio first")
            return
        if not self.recording_id.text().strip() or not self.transcript.text():
            self.error.emit("Recording ID and exact transcript are required")
            return
        request = IngestRequest(
            recording_id=self.recording_id.text().strip(),
            audio_path=self.take_path,
            transcript=self.transcript.text(),
            language=self.language.currentText().strip() or "it",
            speaker_id=self.selected_speaker_id(),
        )
        self.save_requested.emit(request)

    def _update_level(self) -> None:
        self.level.setValue(round(min(1.0, self.recorder.peak_level) * 100))

    def close(self) -> None:
        self.recorder.close()
