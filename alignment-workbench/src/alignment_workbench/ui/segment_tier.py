from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from alignment_workbench.state.editor import EditorSession, EditorTrack, Segment, ToolMode


class SegmentTier(QtWidgets.QWidget):
    segment_selected = QtCore.Signal(str)
    boundary_changed = QtCore.Signal(str, int, int)

    def __init__(
        self,
        session: EditorSession,
        track: EditorTrack,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.track = track
        self._drag: tuple[str, str] | None = None
        self._timeline_left = 0
        self._timeline_width = 1
        self.setFixedHeight(70)
        self.setMouseTracking(True)

    @property
    def timeline_left(self) -> int:
        return self._timeline_left

    @property
    def timeline_width(self) -> int:
        return self._timeline_width

    def set_timeline_geometry(self, left: int, width: int) -> None:
        actual_left = max(0, int(left))
        actual_width = max(1, min(int(width), max(1, self.width() - actual_left)))
        if (actual_left, actual_width) == (self._timeline_left, self._timeline_width):
            return
        self._timeline_left = actual_left
        self._timeline_width = actual_width
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#181b1f"))
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)
        self._paint_row(painter, self.track.words, 0, 34, "Words")
        self._paint_row(painter, self.track.phones, 35, 34, "Sounds")

    def _paint_row(
        self,
        painter: QtGui.QPainter,
        segments: list[Segment],
        top: int,
        height: int,
        title: str,
    ) -> None:
        timeline_right = self._timeline_left + self._timeline_width
        painter.setPen(QtGui.QColor("#656b73"))
        painter.drawLine(self._timeline_left, top + height, timeline_right, top + height)
        if not segments:
            painter.setPen(QtGui.QColor("#757b84"))
            painter.drawText(self._timeline_left + 6, top + 21, title)
            return
        painter.save()
        painter.setClipRect(QtCore.QRectF(self._timeline_left, top, self._timeline_width, height))
        for segment in segments:
            left = self._x(segment.start_frame)
            right = self._x(segment.end_frame)
            if right < self._timeline_left or left > timeline_right:
                continue
            selected = self.session.selected_segment == (self.track.id, segment.id)
            color = QtGui.QColor(self.track.color)
            color.setAlpha(115 if selected else 55)
            painter.fillRect(QtCore.QRectF(left, top + 1, max(1, right - left), height - 2), color)
            painter.setPen(QtGui.QColor("#d9dde3") if selected else QtGui.QColor("#aeb4bc"))
            painter.drawRect(QtCore.QRectF(left, top + 1, max(1, right - left), height - 2))
            painter.drawText(
                QtCore.QRectF(left + 3, top + 3, max(0, right - left - 6), height - 6),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                segment.label,
            )
        painter.restore()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not (
            self._timeline_left
            <= event.position().x()
            <= self._timeline_left + self._timeline_width
        ):
            return
        frame = self._frame(event.position().x())
        segments = self.track.words if event.position().y() < 35 else self.track.phones
        segment = next(
            (item for item in segments if item.start_frame <= frame < item.end_frame), None
        )
        if segment is None:
            return
        self.segment_selected.emit(segment.id)
        if self.session.tool is ToolMode.BOUNDARY:
            tolerance = max(1, round(self.session.viewport.width * 7 / max(1, self.width())))
            edge = (
                "start"
                if abs(frame - segment.start_frame) <= tolerance
                else "end"
                if abs(frame - segment.end_frame) <= tolerance
                else None
            )
            if edge:
                self._drag = (segment.id, edge)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag is None:
            return
        segment_id, edge = self._drag
        segment = self.session.segment(self.track.id, segment_id)
        frame = self._frame(event.position().x())
        minimum = max(1, self.session.sample_rate // 1000)
        start = min(frame, segment.end_frame - minimum) if edge == "start" else segment.start_frame
        end = max(frame, segment.start_frame + minimum) if edge == "end" else segment.end_frame
        self.boundary_changed.emit(segment.id, max(0, start), max(start + minimum, end))
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag = None
        event.accept()

    def _x(self, frame: int) -> float:
        return self._timeline_left + (
            frame - self.session.viewport.start
        ) * self._timeline_width / max(1, self.session.viewport.width)

    def _frame(self, x: float) -> int:
        return round(
            self.session.viewport.start
            + max(0.0, min(float(self._timeline_width), x - self._timeline_left))
            * self.session.viewport.width
            / max(1, self._timeline_width)
        )
