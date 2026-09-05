"""Basic waveform and annotation rendering for the reference client."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6 import QtCore, QtGui, QtWidgets

from models import SignalAnnotation


@dataclass(frozen=True, slots=True)
class WaveformEnvelope:
    minimum: tuple[float, ...]
    maximum: tuple[float, ...]
    frame_count: int
    sample_rate_hz: int


def load_waveform_envelope(path: Path, *, point_count: int = 2_000) -> WaveformEnvelope:
    """Decode a bounded PCM envelope without retaining the full recording."""

    with wave.open(str(path), "rb") as source:
        if source.getcomptype() != "NONE":
            raise ValueError("waveform preview requires uncompressed PCM WAV audio")
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        frame_count = source.getnframes()
        sample_rate_hz = source.getframerate()
        if channels <= 0 or sample_width not in {1, 2, 3, 4} or frame_count <= 0:
            raise ValueError("waveform preview found unsupported PCM metadata")

        bins = max(1, min(point_count, frame_count))
        minimum = [1.0] * bins
        maximum = [-1.0] * bins
        frame_width = channels * sample_width
        frame_offset = 0
        while frame_offset < frame_count:
            data = source.readframes(min(4_096, frame_count - frame_offset))
            chunk_frames = len(data) // frame_width
            if chunk_frames == 0:
                break
            for chunk_index in range(chunk_frames):
                frame_start = chunk_index * frame_width
                mono = 0.0
                for channel in range(channels):
                    sample_start = frame_start + channel * sample_width
                    mono += _pcm_sample(data, sample_start, sample_width)
                mono /= channels
                bin_index = min(
                    bins - 1,
                    ((frame_offset + chunk_index) * bins) // frame_count,
                )
                minimum[bin_index] = min(minimum[bin_index], mono)
                maximum[bin_index] = max(maximum[bin_index], mono)
            frame_offset += chunk_frames

    for index, low in enumerate(minimum):
        if low > maximum[index]:
            minimum[index] = 0.0
            maximum[index] = 0.0
    return WaveformEnvelope(
        minimum=tuple(minimum),
        maximum=tuple(maximum),
        frame_count=frame_count,
        sample_rate_hz=sample_rate_hz,
    )


def _pcm_sample(data: bytes, offset: int, width: int) -> float:
    if width == 1:
        return (data[offset] - 128) / 128.0
    raw = int.from_bytes(data[offset : offset + width], byteorder="little", signed=True)
    return raw / float(1 << (width * 8 - 1))


class WaveformView(QtWidgets.QWidget):
    annotation_selected = QtCore.Signal(object)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        self._envelope: WaveformEnvelope | None = None
        self._annotations: tuple[SignalAnnotation, ...] = ()
        self._frame_count = 1
        self._sample_rate_hz = 1
        self._selected_annotation_id: UUID | None = None
        self._playhead_seconds = 0.0
        self._message = "Open a recording to prepare its waveform"
        self._annotation_rects: dict[UUID, QtCore.QRectF] = {}

    def set_loading(self, message: str = "Preparing waveform…") -> None:
        self._envelope = None
        self._message = message
        self.update()

    def set_error(self, message: str) -> None:
        self._envelope = None
        self._message = message
        self.update()

    def clear(self) -> None:
        self._envelope = None
        self._annotations = ()
        self._selected_annotation_id = None
        self._playhead_seconds = 0.0
        self._message = "Open a recording to prepare its waveform"
        self._annotation_rects.clear()
        self.update()

    def set_recording(
        self,
        envelope: WaveformEnvelope,
        annotations: tuple[SignalAnnotation, ...],
    ) -> None:
        self._envelope = envelope
        self._frame_count = envelope.frame_count
        self._sample_rate_hz = envelope.sample_rate_hz
        self._annotations = annotations
        self._message = ""
        self.update()

    def set_annotations(
        self,
        annotations: tuple[SignalAnnotation, ...],
        *,
        frame_count: int,
        sample_rate_hz: int,
    ) -> None:
        self._annotations = annotations
        self._frame_count = max(1, frame_count)
        self._sample_rate_hz = max(1, sample_rate_hz)
        self.update()

    def set_selected_annotation(self, annotation_id: UUID | None) -> None:
        self._selected_annotation_id = annotation_id
        self.update()

    def set_playhead_seconds(self, seconds: float) -> None:
        self._playhead_seconds = max(0.0, seconds)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#15191f"))
        bounds = QtCore.QRectF(self.rect()).adjusted(10.0, 10.0, -10.0, -22.0)
        lane = QtCore.QRectF(bounds.left(), bounds.top(), bounds.width(), 42.0)
        wave_bounds = QtCore.QRectF(
            bounds.left(),
            lane.bottom() + 7.0,
            bounds.width(),
            max(20.0, bounds.bottom() - lane.bottom() - 7.0),
        )
        self._paint_annotations(painter, lane)
        self._paint_waveform(painter, wave_bounds)
        self._paint_timeline(painter, wave_bounds)
        self._paint_playhead(painter, bounds)

    def _paint_annotations(self, painter: QtGui.QPainter, lane: QtCore.QRectF) -> None:
        painter.fillRect(lane, QtGui.QColor("#202731"))
        self._annotation_rects.clear()
        colors = ("#5DADE2", "#58D68D", "#F5B041", "#AF7AC5", "#EC7063")
        for index, annotation in enumerate(self._annotations):
            geometry = annotation.geometry
            end_sample = geometry.end_sample
            start_x = lane.left() + lane.width() * geometry.start_sample / self._frame_count
            if end_sample is None:
                end_x = start_x + 3.0
            else:
                end_x = lane.left() + lane.width() * end_sample / self._frame_count
            rect = QtCore.QRectF(
                start_x,
                lane.top() + 4.0,
                max(3.0, end_x - start_x),
                lane.height() - 8.0,
            ).intersected(lane)
            self._annotation_rects[annotation.id] = rect
            color = QtGui.QColor(colors[index % len(colors)])
            color.setAlpha(115)
            painter.fillRect(rect, color)
            selected = annotation.id == self._selected_annotation_id
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffffff" if selected else "#aab4c0"), 2))
            painter.drawRect(rect)
            if rect.width() > 45:
                painter.setPen(QtGui.QColor("#f5f7fa"))
                painter.drawText(
                    rect.adjusted(4.0, 0.0, -2.0, 0.0),
                    QtCore.Qt.AlignmentFlag.AlignVCenter
                    | QtCore.Qt.AlignmentFlag.AlignLeft,
                    annotation.concept_ref.entry_key,
                )

    def _paint_waveform(self, painter: QtGui.QPainter, bounds: QtCore.QRectF) -> None:
        painter.fillRect(bounds, QtGui.QColor("#0e1217"))
        center = bounds.center().y()
        painter.setPen(QtGui.QColor("#4b5563"))
        painter.drawLine(
            QtCore.QPointF(bounds.left(), center),
            QtCore.QPointF(bounds.right(), center),
        )
        envelope = self._envelope
        if envelope is None:
            painter.setPen(QtGui.QColor("#aeb7c2"))
            painter.drawText(bounds, QtCore.Qt.AlignmentFlag.AlignCenter, self._message)
            return
        count = len(envelope.minimum)
        if count == 0:
            return
        amplitude = bounds.height() * 0.47
        path = QtGui.QPainterPath()
        for index, high in enumerate(envelope.maximum):
            x = bounds.left() + bounds.width() * index / max(1, count - 1)
            y = center - high * amplitude
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        for index in range(count - 1, -1, -1):
            x = bounds.left() + bounds.width() * index / max(1, count - 1)
            path.lineTo(x, center - envelope.minimum[index] * amplitude)
        path.closeSubpath()
        painter.fillPath(path, QtGui.QColor("#3a86c8"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#7cc7ff"), 1))
        painter.drawPath(path)

    def _paint_timeline(self, painter: QtGui.QPainter, bounds: QtCore.QRectF) -> None:
        duration = self._frame_count / self._sample_rate_hz
        painter.setPen(QtGui.QColor("#8f9aa8"))
        for index in range(6):
            fraction = index / 5
            x = bounds.left() + bounds.width() * fraction
            painter.drawLine(
                QtCore.QPointF(x, bounds.bottom()),
                QtCore.QPointF(x, bounds.bottom() + 4.0),
            )
            label = f"{duration * fraction:.2f}s"
            painter.drawText(
                QtCore.QRectF(x - 28.0, bounds.bottom() + 4.0, 56.0, 16.0),
                QtCore.Qt.AlignmentFlag.AlignHCenter,
                label,
            )

    def _paint_playhead(self, painter: QtGui.QPainter, bounds: QtCore.QRectF) -> None:
        duration = self._frame_count / self._sample_rate_hz
        if duration <= 0:
            return
        fraction = min(1.0, self._playhead_seconds / duration)
        x = bounds.left() + bounds.width() * fraction
        painter.setPen(QtGui.QPen(QtGui.QColor("#ff5d73"), 2))
        painter.drawLine(
            QtCore.QPointF(x, bounds.top()),
            QtCore.QPointF(x, bounds.bottom()),
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            candidates = [
                (rect.width(), annotation_id)
                for annotation_id, rect in self._annotation_rects.items()
                if rect.contains(event.position())
            ]
            if candidates:
                self.annotation_selected.emit(min(candidates)[1])
                event.accept()
                return
        super().mousePressEvent(event)
