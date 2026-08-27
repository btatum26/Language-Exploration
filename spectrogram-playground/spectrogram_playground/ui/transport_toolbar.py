from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class TransportToolbar(QtWidgets.QToolBar):
    add_requested = QtCore.Signal()
    record_requested = QtCore.Signal()
    play_requested = QtCore.Signal()
    stop_requested = QtCore.Signal()
    beginning_requested = QtCore.Signal()
    end_requested = QtCore.Signal()
    loop_changed = QtCore.Signal(bool)
    follow_changed = QtCore.Signal(bool)
    zoom_in_requested = QtCore.Signal()
    zoom_out_requested = QtCore.Signal()
    zoom_selection_requested = QtCore.Signal()
    fit_requested = QtCore.Signal()
    reset_requested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("Transport", parent)
        self.setMovable(False)
        self._action("Add", self.add_requested, "Add audio track")
        self.record_action = self._action("Record", self.record_requested, "Record microphone")
        self._action("|◀", self.beginning_requested, "Jump to beginning")
        self.play_action = self._action("▶ / ❚❚", self.play_requested, "Play or pause")
        self._action("■", self.stop_requested, "Stop")
        self._action("▶|", self.end_requested, "Jump to end")
        self.addSeparator()
        self.time_label = QtWidgets.QLabel(" 00:00.000 ")
        self.time_label.setMinimumWidth(90)
        self.addWidget(self.time_label)
        self.recording_label = QtWidgets.QLabel(" REC 00:00.0 ")
        self.recording_label.setStyleSheet("color: #ff6b6b; font-weight: bold")
        self.recording_label.setVisible(False)
        self.addWidget(self.recording_label)
        self.input_level = QtWidgets.QProgressBar()
        self.input_level.setRange(0, 100)
        self.input_level.setValue(0)
        self.input_level.setTextVisible(False)
        self.input_level.setFixedWidth(70)
        self.input_level.setToolTip("Microphone input peak level")
        self.input_level.setVisible(False)
        self.addWidget(self.input_level)
        self.loop_action = self._action("Loop", self.loop_changed, "Loop selection", checkable=True)
        self.follow_action = self._action(
            "Follow", self.follow_changed, "Follow playhead", checkable=True
        )
        self.follow_action.setChecked(True)
        self.addSeparator()
        self._action("+", self.zoom_in_requested, "Zoom in")
        self._action("−", self.zoom_out_requested, "Zoom out")
        self._action("Zoom selection", self.zoom_selection_requested, "Zoom to selection")
        self._action("Fit", self.fit_requested, "Fit project")
        self._action("Reset", self.reset_requested, "Reset zoom")

    def _action(
        self, text: str, signal: object, tip: str, checkable: bool = False
    ) -> QtGui.QAction:
        action = QtGui.QAction(text, self, checkable=checkable)
        action.setToolTip(tip)
        if checkable:
            action.toggled.connect(signal.emit)  # type: ignore[attr-defined]
        else:
            action.triggered.connect(signal.emit)  # type: ignore[attr-defined]
        self.addAction(action)
        return action

    def set_time(self, seconds: float) -> None:
        minutes, remaining = divmod(max(0.0, seconds), 60)
        self.time_label.setText(f" {int(minutes):02d}:{remaining:06.3f} ")

    def set_recording_feedback(
        self, active: bool, seconds: float = 0.0, level: float = 0.0
    ) -> None:
        self.recording_label.setVisible(active)
        self.input_level.setVisible(active)
        if active:
            minutes, remaining = divmod(max(0.0, seconds), 60)
            self.recording_label.setText(f" ● REC {int(minutes):02d}:{remaining:04.1f} ")
            self.input_level.setValue(min(100, max(0, round(level * 100))))
