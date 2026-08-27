from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets
from spectrogram_playground.analysis.spectrum import SpectrumResult, calculate_spectrum

from alignment_workbench.services.tasks import TaskManager
from alignment_workbench.state.editor import EditorSession


class SpectrumPanel(QtWidgets.QWidget):
    def __init__(
        self,
        session: EditorSession,
        tasks: TaskManager,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.tasks = tasks
        self._color = "#59a5d8"
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.scale = QtWidgets.QComboBox()
        self.scale.addItems(("Linear frequency", "Log frequency"))
        self.quantity = QtWidgets.QComboBox()
        self.quantity.addItems(("Decibels", "Magnitude", "Power"))
        refresh = QtWidgets.QPushButton("Analyze selection")
        refresh.clicked.connect(self.refresh)
        controls.addWidget(self.scale)
        controls.addWidget(self.quantity)
        controls.addWidget(refresh)
        layout.addLayout(controls)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#15171a")
        layout.addWidget(self.plot)
        self.status = QtWidgets.QLabel("Select audio to analyze")
        layout.addWidget(self.status)
        tasks.completed.connect(self._task_completed)
        tasks.failed.connect(self._task_failed)

    def refresh(self) -> None:
        track = self.session.active_track
        if track is None or not self.session.selection.active:
            self.status.setText("Select audio to analyze")
            return
        assert self.session.selection.start is not None and self.session.selection.end is not None
        samples = self.session.render_track(track.id)[
            self.session.selection.start : self.session.selection.end
        ]
        self._color = track.color
        self.status.setText("Analyzing selectionâ€¦")
        self.tasks.submit(
            "spectrum",
            lambda _cancel, _progress: calculate_spectrum(samples, self.session.sample_rate),
            replace=True,
        )

    @QtCore.Slot(str, object, object)
    def _task_completed(self, category: str, _request_id: object, result: object) -> None:
        if category != "spectrum" or not isinstance(result, SpectrumResult):
            return
        values = {
            "Decibels": result.decibels,
            "Magnitude": result.magnitude,
            "Power": result.power,
        }[self.quantity.currentText()]
        self.plot.clear()
        self.plot.plot(result.frequencies, values, pen=pg.mkPen(self._color, width=1.5))
        self.plot.setLogMode(x=self.scale.currentIndex() == 1, y=False)
        self.status.setText(result.method)

    @QtCore.Slot(str, object, str)
    def _task_failed(self, category: str, _request_id: object, message: str) -> None:
        if category == "spectrum":
            self.status.setText(message)
