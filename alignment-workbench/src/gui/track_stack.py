"""Tracks retain their own heights inside a vertically scrolling workspace."""

from PySide6 import QtCore, QtGui, QtWidgets

STANDARD_TRACK_HEIGHT = 280


class TrackStack(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.rows = QtWidgets.QVBoxLayout(self)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.rows.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        self.rows.addStretch(1)

    def addWidget(self, widget: QtWidgets.QWidget) -> None:
        self.insertWidget(self.rows.count() - 1, widget)

    def insertWidget(self, index: int, widget: QtWidgets.QWidget) -> None:
        if self.rows.indexOf(widget) == index:
            return
        self.rows.removeWidget(widget)
        self.rows.insertWidget(index, widget)


class TrackResizeHandle(QtWidgets.QFrame):
    def __init__(self, track: QtWidgets.QWidget) -> None:
        super().__init__(track)
        self.track = track
        self.minimum_height = 220
        self.setFixedHeight(7)
        self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag to resize this track")
        self.setStyleSheet("background: #637180;")
        self._press_y: float | None = None
        self._height = STANDARD_TRACK_HEIGHT

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._press_y = event.globalPosition().y()
            self._height = self.track.height()
            event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press_y is not None:
            height = self._height + round(event.globalPosition().y() - self._press_y)
            self.track.setFixedHeight(max(self.minimum_height, height))
            event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self.mouseMoveEvent(event)
        self._press_y = None
