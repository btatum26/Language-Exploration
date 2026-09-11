"""Microphone take capture, streamed to a temporary PCM WAV file."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtMultimedia, QtWidgets


class MicrophoneDialog(QtWidgets.QDialog):
    add_requested = QtCore.Signal()

    def __init__(self, path: Path, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.source: QtMultimedia.QAudioSource | None = None
        self.stream: QtCore.QIODevice | None = None
        self.writer: wave.Wave_write | None = None
        self.frames = 0
        self._tail = b""
        self._saving = False
        self._failed = False
        self.format = QtMultimedia.QAudioFormat()
        self.setWindowTitle("Record new audio")
        self.setMinimumWidth(440)
        layout = QtWidgets.QFormLayout(self)
        self.device = QtWidgets.QComboBox()
        self.devices = QtMultimedia.QMediaDevices.audioInputs()
        default = QtMultimedia.QMediaDevices.defaultAudioInput()
        for index, device in enumerate(self.devices):
            self.device.addItem(device.description())
            if device.id() == default.id():
                self.device.setCurrentIndex(index)
        self.name = QtWidgets.QLineEdit("Microphone recording")
        self.language = QtWidgets.QLineEdit("und")
        self.status = QtWidgets.QLabel("Ready" if self.devices else "No microphone available.")
        self.status.setWordWrap(True)
        self.level = QtWidgets.QProgressBar()
        self.level.setRange(0, 100)
        self.level.setValue(0)
        self.level.setFormat("Input level: %p%")
        self.start_button = QtWidgets.QPushButton("Start recording")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.add_button = QtWidgets.QPushButton("Add recording")
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        layout.addRow("Microphone", self.device)
        layout.addRow("Name", self.name)
        layout.addRow("Language tag", self.language)
        layout.addRow(self.status)
        layout.addRow(self.level)
        for button in (self.start_button, self.stop_button, self.add_button, self.cancel_button):
            button.setAutoDefault(False)
            layout.addRow(button)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.add_button.clicked.connect(self._add)
        self.cancel_button.clicked.connect(self.reject)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._read)
        self._update()

    def _update(self) -> None:
        active = self.source is not None
        self.start_button.setEnabled(bool(self.devices) and not active and not self._saving)
        self.start_button.setText("Record again" if self.frames else "Start recording")
        self.stop_button.setEnabled(active and not self._saving)
        self.device.setEnabled(not active and not self._saving)
        self.add_button.setEnabled(bool(self.frames) and not active and not self._saving
                                   and not self._failed)
        self.cancel_button.setEnabled(not self._saving)
        self.name.setEnabled(not self._saving)
        self.language.setEnabled(not self._saving)

    def start(self) -> None:
        if self.source is not None or self._saving or not self.devices:
            return
        device = self.devices[self.device.currentIndex()]
        self.format = device.preferredFormat()
        self.frames, self._tail, self._failed = 0, b"", False
        self.level.setValue(0)
        try:
            if not self.format.isValid() or not device.isFormatSupported(self.format):
                raise ValueError("The microphone has no supported audio format.")
            self.writer = wave.open(str(self.path), "wb")
            self.writer.setnchannels(self.format.channelCount())
            self.writer.setsampwidth(2)
            self.writer.setframerate(self.format.sampleRate())
            self.source = QtMultimedia.QAudioSource(device, self.format, self)
            self.stream = self.source.start()
            if self.stream is None or self.source.error() != QtMultimedia.QtAudio.Error.NoError:
                raise RuntimeError(
                    "Cannot open microphone. Check input device and microphone access."
                )
            self.status.setText("Recording · 0.0 s")
            self.timer.start()
        except (OSError, ValueError, RuntimeError, wave.Error) as exc:
            self._fail(str(exc))
        self._update()

    def _read(self) -> None:
        if self.stream is None or self.source is None:
            return
        if self.source.error() != QtMultimedia.QtAudio.Error.NoError:
            self._fail("Microphone capture failed. Check the device and record again.")
            return
        try:
            # Bound each read; retain incomplete frames until the next device chunk.
            data = self._tail + self.stream.read(256 * 1024).data()
            size = self.format.bytesPerFrame()
            end = len(data) - len(data) % size
            self._tail = data[end:]
            if not end:
                self.level.setValue(0)
                return
            kind = self.format.sampleFormat()
            formats = QtMultimedia.QAudioFormat.SampleFormat
            dtype = {formats.UInt8: "u1", formats.Int16: "=i2",
                     formats.Int32: "=i4", formats.Float: "=f4"}[kind]
            samples = np.frombuffer(data[:end], dtype=dtype).astype(np.float64)
            if kind == formats.UInt8:
                samples = (samples - 128) / 128
            elif kind == formats.Int16:
                samples /= 32768
            elif kind == formats.Int32:
                samples /= 2147483648
            samples = np.nan_to_num(samples)
            peak = float(np.max(np.abs(samples)))
            pcm = np.clip(np.rint(samples * 32768), -32768, 32767).astype("<i2")
            assert self.writer is not None
            self.writer.writeframesraw(pcm.tobytes())
            self.frames += end // size
            self.level.setValue(min(100, round(peak * 100)))
            self.status.setText(f"Recording · {self.frames / self.format.sampleRate():.1f} s"
                                + (" · Clipping" if peak >= 0.999 else ""))
        except (OSError, ValueError, KeyError, wave.Error) as exc:
            self._fail(f"Could not write recording: {exc}")

    def stop(self) -> None:
        if self.stream is not None:
            self._read()
        self._release()
        if not self._failed:
            self.status.setText(f"Stopped · {self.frames / max(1, self.format.sampleRate()):.1f} s"
                                if self.frames else "No audio received. Check the microphone.")
        self._update()

    def _release(self) -> None:
        self.timer.stop()
        self.stream = None
        if self.source is not None:
            self.source.stop()
            self.source.deleteLater()
            self.source = None
        if self.writer is not None:
            writer, self.writer = self.writer, None
            try:
                writer.close()
            except (OSError, wave.Error) as exc:
                self._failed = True
                self.status.setText(f"Could not finalize recording: {exc}")

    def _fail(self, message: str) -> None:
        self._failed = True
        self._release()
        self.status.setText(message)
        self._update()

    def set_saving(self, saving: bool) -> None:
        self._saving = saving
        self._update()

    def _add(self) -> None:
        if not self.name.text().strip() or not self.language.text().strip():
            self.status.setText("Enter a recording name and language tag.")
            return
        self.add_requested.emit()

    def reject(self) -> None:
        if self._saving:
            return
        self._release()
        super().reject()
