from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


class InteractivePlot(pg.PlotWidget):
    seek_requested = QtCore.Signal(float)
    selection_requested = QtCore.Signal(float, float)
    pan_requested = QtCore.Signal(float)
    zoom_requested = QtCore.Signal(float, float)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._press_time: float | None = None
        self._last_time: float | None = None
        self._last_position: QtCore.QPointF | None = None
        self._interaction: str | None = None
        self.setMouseEnabled(x=False, y=False)
        self.setMenuEnabled(False)
        self.setBackground("#15171a")
        self.showGrid(x=True, y=False, alpha=0.15)

    def time_at(self, position: QtCore.QPoint) -> float:
        scene_position = self.mapToScene(position)
        return float(self.plotItem.vb.mapSceneToView(scene_position).x())

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._interaction = "pan"
        elif event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._interaction = (
                "select"
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
                else "seek"
            )
        else:
            return super().mousePressEvent(event)
        time = self.time_at(event.position().toPoint())
        self._press_time = self._last_time = time
        self._last_position = event.position()
        if self._interaction == "seek":
            self.seek_requested.emit(time)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press_time is None:
            return super().mouseMoveEvent(event)
        time = self.time_at(event.position().toPoint())
        if self._interaction == "seek":
            self.seek_requested.emit(time)
        elif self._interaction == "select" and time != self._press_time:
            self.selection_requested.emit(self._press_time, time)
        elif self._interaction == "pan" and self._last_position is not None:
            view_width = self.plotItem.vb.sceneBoundingRect().width()
            if view_width > 0:
                pixel_delta = event.position().x() - self._last_position.x()
                view_start, view_end = self.plotItem.vb.viewRange()[0]
                seconds_per_pixel = view_end - view_start
                self.pan_requested.emit(-pixel_delta * seconds_per_pixel / view_width)
        self._last_time = time
        self._last_position = event.position()
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press_time is not None:
            self.mouseMoveEvent(event)
            self._press_time = self._last_time = self._last_position = None
            self._interaction = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        factor = 0.8 if event.angleDelta().y() > 0 else 1.25
        self.zoom_requested.emit(factor, self.time_at(event.position().toPoint()))
        event.accept()


def add_timeline_items(plot: pg.PlotWidget) -> tuple[pg.InfiniteLine, pg.LinearRegionItem]:
    region = pg.LinearRegionItem(
        values=(0, 0),
        movable=False,
        brush=pg.mkBrush(89, 165, 216, 45),
        pen=pg.mkPen("#6ab7e8", width=1),
    )
    region.setVisible(False)
    playhead = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#ffcc66", width=2))
    plot.addItem(region, ignoreBounds=True)
    plot.addItem(playhead, ignoreBounds=True)
    return playhead, region
