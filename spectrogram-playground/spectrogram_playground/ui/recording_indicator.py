from __future__ import annotations

from PySide6 import QtCore, QtWidgets


class RecordingIndicator(QtWidgets.QFrame):
    stop_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("recordingIndicator")
        self.setStyleSheet(
            "#recordingIndicator { background: #351d22; border: 1px solid #a43d4b; }"
            "#recordingIndicator QLabel { background: transparent; }"
        )
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        self.status = QtWidgets.QLabel("● RECORDING 00:00.0")
        self.status.setStyleSheet("color: #ff6677; font-weight: bold")
        self.device = QtWidgets.QLabel()
        self.device.setStyleSheet("color: #d9dde3")
        self.level = QtWidgets.QProgressBar()
        self.level.setRange(0, 100)
        self.level.setValue(0)
        self.level.setFormat("Input %p%")
        self.level.setMinimumWidth(180)
        self.level.setAccessibleName("Live microphone input level")
        stop = QtWidgets.QPushButton("Stop recording")
        stop.clicked.connect(self.stop_requested)
        layout.addWidget(self.status)
        layout.addWidget(self.device, 1)
        layout.addWidget(self.level)
        layout.addWidget(stop)
        self.setVisible(False)

    def update_recording(self, seconds: float, peak_level: float, device_name: str) -> None:
        minutes, remaining = divmod(max(0.0, seconds), 60)
        self.status.setText(f"● RECORDING {int(minutes):02d}:{remaining:04.1f}")
        self.device.setText(device_name)
        self.level.setValue(min(100, max(0, round(peak_level * 100))))
