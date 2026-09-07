"""Basic waveform and annotation rendering for the reference client."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import soundfile as sf  # type: ignore[import-untyped]
from PySide6 import QtCore, QtGui, QtWidgets

from models import SignalAnnotation


def annotation_label(annotation: SignalAnnotation) -> str:
    return (annotation.label or "").strip() or "Unlabeled"


@dataclass(frozen=True, slots=True)
class WaveformEnvelope:
    minimum: tuple[float, ...]
    maximum: tuple[float, ...]
    frame_count: int
    sample_rate_hz: int
    start_sample: int = 0
    end_sample: int | None = None


def load_waveform_envelope(path: Path, *, point_count: int = 2_000) -> WaveformEnvelope:
    """Decode a bounded WAV or MP3 envelope without retaining the full recording."""

    if path.suffix.lower() == ".mp3":
        return _load_mp3_envelope(path, point_count=point_count)
    return _load_wav_envelope(path, point_count=point_count)


def _load_wav_envelope(path: Path, *, point_count: int) -> WaveformEnvelope:
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

    _fill_empty_bins(minimum, maximum)
    return WaveformEnvelope(
        minimum=tuple(minimum),
        maximum=tuple(maximum),
        frame_count=frame_count,
        sample_rate_hz=sample_rate_hz,
    )


def _load_mp3_envelope(path: Path, *, point_count: int) -> WaveformEnvelope:
    try:
        with sf.SoundFile(path) as source:
            if source.format != "MP3" or source.subtype != "MPEG_LAYER_III":
                raise ValueError("waveform preview requires MPEG Layer III audio")
            channels = int(source.channels)
            frame_count = int(len(source))
            sample_rate_hz = int(source.samplerate)
            if channels <= 0 or frame_count <= 0 or sample_rate_hz <= 0:
                raise ValueError("waveform preview found invalid MP3 metadata")

            bins = max(1, min(point_count, frame_count))
            minimum = [1.0] * bins
            maximum = [-1.0] * bins
            frame_offset = 0
            while frame_offset < frame_count:
                block = source.read(
                    frames=min(4_096, frame_count - frame_offset),
                    dtype="float32",
                    always_2d=True,
                )
                if len(block) == 0:
                    break
                for chunk_index, frame in enumerate(block):
                    mono = float(frame.sum()) / channels
                    bin_index = min(
                        bins - 1,
                        ((frame_offset + chunk_index) * bins) // frame_count,
                    )
                    minimum[bin_index] = min(minimum[bin_index], mono)
                    maximum[bin_index] = max(maximum[bin_index], mono)
                frame_offset += len(block)
    except (OSError, RuntimeError) as exc:
        raise ValueError("waveform preview could not decode MP3 audio") from exc

    _fill_empty_bins(minimum, maximum)
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


def _fill_empty_bins(minimum: list[float], maximum: list[float]) -> None:
    for index, low in enumerate(minimum):
        if low > maximum[index]:
            minimum[index] = 0.0
            maximum[index] = 0.0


def load_waveform_detail(
    path: Path,
    start: int,
    end: int,
    *,
    point_count: int = 2000,
) -> WaveformEnvelope:
    """Decode only a settled viewport, in bounded blocks on a worker thread."""
    with sf.SoundFile(path) as source:
        frame_count = len(source)
        start = max(0, min(start, frame_count - 1))
        end = max(start + 1, min(end, frame_count))
        bins = min(max(1, point_count), end - start)
        minimum = [1.0] * bins
        maximum = [-1.0] * bins
        source.seek(start)
        offset = start
        while offset < end:
            block = source.read(min(65536, end - offset), dtype="float32", always_2d=True)
            if not len(block):
                break
            for index, mono in enumerate(block.mean(axis=1).tolist()):
                bucket = (offset + index - start) * bins // (end - start)
                minimum[bucket] = min(minimum[bucket], mono)
                maximum[bucket] = max(maximum[bucket], mono)
            offset += len(block)
        _fill_empty_bins(minimum, maximum)
        return WaveformEnvelope(
            tuple(minimum), tuple(maximum), frame_count, int(source.samplerate), start, end
        )


class WaveformView(QtWidgets.QWidget):
    annotation_selected = QtCore.Signal(object)
    selection_changed = QtCore.Signal(object)
    seek_requested = QtCore.Signal(int)
    boundary_committed = QtCore.Signal(object, int, int)
    viewport_changed = QtCore.Signal(int, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding
        )
        self._envelope: WaveformEnvelope | None = None
        self._detail: WaveformEnvelope | None = None
        self._annotations: tuple[SignalAnnotation, ...] = ()
        self._frame_count = 1
        self._sample_rate_hz = 1
        self._selected_annotation_id: UUID | None = None
        self._playhead_seconds = 0.0
        self._message = "Open a recording to prepare its waveform"
        self._annotation_rects: dict[UUID, QtCore.QRectF] = {}
        self.viewport = (0, 1)
        self.selection: tuple[int, int] | None = None
        self._editable = True
        self._press: QtCore.QPointF | None = None
        self._dragged = False
        self._gesture = ""
        self._original_selection: tuple[int, int] | None = None
        self._original_viewport = (0, 1)
        self._boundary: tuple[UUID, int, int, str] | None = None
        self._preview: tuple[int, int] | None = None

    def set_loading(self, message: str = "Preparing waveform...") -> None:
        self.clear()
        self._message = message

    def set_error(self, message: str) -> None:
        self._envelope = None
        self._message = message
        self.update()

    def clear(self) -> None:
        self.cancel_gesture()
        self._envelope = self._detail = None
        self._annotations = ()
        self._selected_annotation_id = None
        self._playhead_seconds = 0.0
        self._message = "Open a recording to prepare its waveform"
        self._annotation_rects.clear()
        self.clear_selection()
        self.update()

    def set_recording(
        self, envelope: WaveformEnvelope, annotations: tuple[SignalAnnotation, ...]
    ) -> None:
        self._envelope = envelope
        self._frame_count = envelope.frame_count
        self._sample_rate_hz = envelope.sample_rate_hz
        self._annotations = annotations
        self._message = ""
        self.fit_recording()

    def set_detail(self, envelope: WaveformEnvelope) -> None:
        self._detail = envelope
        self.update()

    def set_annotations(
        self, annotations: tuple[SignalAnnotation, ...], *, frame_count: int, sample_rate_hz: int
    ) -> None:
        self._annotations = annotations
        self._frame_count = max(1, frame_count)
        self._sample_rate_hz = max(1, sample_rate_hz)
        self._preview = None
        self.update()

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        if not editable:
            self.cancel_gesture()

    def set_selected_annotation(self, annotation_id: UUID | None) -> None:
        self._selected_annotation_id = annotation_id
        self.update()

    def set_playhead_seconds(self, seconds: float) -> None:
        self._playhead_seconds = max(0.0, seconds)
        self.update()

    def sample_to_x(self, sample: float) -> float:
        start, end = self.viewport
        return 10 + (self.width() - 20) * (sample - start) / max(1, end - start)

    def x_to_sample(self, x: float) -> int:
        start, end = self.viewport
        sample = round(start + (x - 10) * (end - start) / max(1, self.width() - 20))
        return max(0, min(self._frame_count, sample))

    def set_viewport(self, start: int, end: int) -> None:
        span = max(1, min(self._frame_count, end - start))
        start = max(0, min(self._frame_count - span, start))
        self.viewport = (start, start + span)
        self.update()
        self.viewport_changed.emit(*self.viewport)

    def fit_recording(self) -> None:
        self.set_viewport(0, self._frame_count)

    def zoom_to_selection(self) -> None:
        if self.selection:
            self.set_viewport(*self.selection)

    def zoom(self, factor: float, anchor: int | None = None) -> None:
        start, end = self.viewport
        if anchor is None:
            anchor = (start + end) // 2
        self.set_viewport(
            round(anchor + (start - anchor) * factor), round(anchor + (end - anchor) * factor)
        )

    def clear_selection(self) -> None:
        self.selection = None
        self.selection_changed.emit(None)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#15191f"))
        bounds = QtCore.QRectF(self.rect()).adjusted(10, 10, -10, -25)
        painter.save()
        painter.setClipRect(bounds)
        lane = QtCore.QRectF(bounds.left(), bounds.top(), bounds.width(), 36)
        painter.fillRect(lane, QtGui.QColor("#202731"))
        self._annotation_rects.clear()
        for annotation in self._annotations:
            start = annotation.geometry.start_sample
            end = annotation.geometry.end_sample
            selected = annotation.id == self._selected_annotation_id
            if selected and self._preview:
                start, end = self._preview
            x = self.sample_to_x(start)
            right = self.sample_to_x(end) if end is not None else x + 3
            rect = QtCore.QRectF(x, lane.top() + 3, max(3, right - x), lane.height() - 6)
            self._annotation_rects[annotation.id] = rect.intersected(lane)
            painter.fillRect(rect, QtGui.QColor("#337ba0" if selected else "#345548"))
            painter.setPen(QtGui.QPen(QtGui.QColor("white" if selected else "#68aa92"), 2))
            painter.drawRect(rect)
            if rect.width() > 45:
                painter.drawText(
                    rect.adjusted(5, 0, 0, 0),
                    QtCore.Qt.AlignmentFlag.AlignVCenter,
                    annotation_label(annotation),
                )
        wave_bounds = bounds.adjusted(0, 44, 0, 0)
        self._paint_waveform(painter, wave_bounds)
        if self.selection:
            left, right = (self.sample_to_x(v) for v in self.selection)
            painter.fillRect(
                QtCore.QRectF(left, wave_bounds.top(), right - left, wave_bounds.height()),
                QtGui.QColor(90, 170, 255, 65),
            )
        x = self.sample_to_x(self._playhead_seconds * self._sample_rate_hz)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ff5d73"), 2))
        painter.drawLine(QtCore.QPointF(x, bounds.top()), QtCore.QPointF(x, bounds.bottom()))
        painter.restore()
        painter.setPen(QtGui.QColor("#aeb7c2"))
        start, end = self.viewport
        for index in range(6):
            sample = start + (end - start) * index / 5
            x = self.sample_to_x(sample)
            painter.drawText(
                QtCore.QRectF(max(0, min(self.width() - 70, x - 35)), bounds.bottom() + 3, 70, 20),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                f"{sample / self._sample_rate_hz:.3f}s",
            )

    def _paint_waveform(self, painter: QtGui.QPainter, bounds: QtCore.QRectF) -> None:
        envelope = self._envelope
        if envelope is None:
            painter.setPen(QtGui.QColor("#aeb7c2"))
            painter.drawText(bounds, QtCore.Qt.AlignmentFlag.AlignCenter, self._message)
            return
        if self._detail and (self._detail.start_sample, self._detail.end_sample) == self.viewport:
            envelope = self._detail
        end = envelope.end_sample or envelope.frame_count
        count = len(envelope.minimum)
        center, amplitude = bounds.center().y(), bounds.height() * 0.47
        painter.setPen(QtGui.QPen(QtGui.QColor("#7cc7ff"), 1))
        previous: QtCore.QPointF | None = None
        for index, (low, high) in enumerate(zip(envelope.minimum, envelope.maximum, strict=True)):
            sample = envelope.start_sample + (end - envelope.start_sample) * index / max(1, count)
            x = self.sample_to_x(sample)
            if x < bounds.left() - 2 or x > bounds.right() + 2:
                continue
            point = QtCore.QPointF(x, center - high * amplitude)
            painter.drawLine(point, QtCore.QPointF(x, center - low * amplitude))
            if previous is not None:
                painter.drawLine(previous, point)
            previous = point

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._envelope is None or not self._editable:
            return
        self.setFocus()
        self._press = event.position()
        self._dragged = False
        self._original_selection = self.selection
        self._original_viewport = self.viewport
        self._gesture = "select"
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._gesture = "pan"
        elif event.button() != QtCore.Qt.MouseButton.LeftButton:
            self._press = None
            return
        elif event.position().y() <= 46:
            # Input can arrive before the next repaint after undo/redo. Hit-test
            # authoritative samples, never rectangles left over from a paint.
            self._annotation_rects = {}
            for annotation in self._annotations:
                left = self.sample_to_x(annotation.geometry.start_sample)
                end = annotation.geometry.end_sample
                right = self.sample_to_x(end) if end is not None else left + 3
                self._annotation_rects[annotation.id] = QtCore.QRectF(
                    left,
                    13,
                    max(3, right - left),
                    30,
                ).intersected(QtCore.QRectF(10, 10, self.width() - 20, 36))
            candidates = [
                (rect.width(), str(key), key)
                for key, rect in self._annotation_rects.items()
                if rect.adjusted(-6, 0, 6, 0).contains(event.position())
            ]
            if candidates:
                annotation_id = min(candidates)[2]
                self.annotation_selected.emit(annotation_id)
                annotation = next(a for a in self._annotations if a.id == annotation_id)
                self._gesture = "annotation"
                geom = annotation.geometry
                if geom.type == "time_interval" and geom.end_sample is not None:
                    edges = [
                        (abs(event.position().x() - self.sample_to_x(geom.start_sample)), "start"),
                        (abs(event.position().x() - self.sample_to_x(geom.end_sample)), "end"),
                    ]
                    distance, edge = min(edges)
                    if distance <= 7:
                        self._gesture = "boundary"
                        self._boundary = (annotation_id, geom.start_sample, geom.end_sample, edge)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press is None:
            return
        if (
            event.position() - self._press
        ).manhattanLength() >= QtWidgets.QApplication.startDragDistance():
            self._dragged = True
        if not self._dragged:
            return
        sample = self.x_to_sample(event.position().x())
        if self._gesture == "select":
            anchor = self.x_to_sample(self._press.x())
            self.selection = (
                (min(anchor, sample), max(anchor, sample)) if anchor != sample else None
            )
            self.selection_changed.emit(self.selection)
        elif self._gesture == "boundary" and self._boundary:
            _, start, end, edge = self._boundary
            self._preview = (sample, end) if edge == "start" else (start, sample)
        elif self._gesture == "pan":
            start, end = self._original_viewport
            delta = round(
                (self._press.x() - event.position().x()) * (end - start) / max(1, self.width() - 20)
            )
            self.set_viewport(start + delta, end + delta)
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._press is None:
            return
        self.mouseMoveEvent(event)
        if self._gesture == "boundary" and self._dragged and self._preview and self._boundary:
            annotation_id, start, end, _ = self._boundary
            preview = self._preview
            self._preview = None
            if preview != (start, end):
                self.boundary_committed.emit(annotation_id, *preview)
        elif self._gesture == "select" and not self._dragged:
            sample = self.x_to_sample(event.position().x())
            self.set_playhead_seconds(sample / self._sample_rate_hz)
            self.seek_requested.emit(sample)
        self._press = None
        self._boundary = None
        self._preview = None
        self.update()

    def cancel_gesture(self) -> None:
        if self._press is not None and self._gesture == "select":
            self.selection = self._original_selection
            self.selection_changed.emit(self.selection)
        self._press = None
        self._boundary = None
        self._preview = None
        self.update()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.cancel_gesture()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._envelope is None or self._press is not None:
            return
        steps = event.angleDelta().y() / 120
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            start, end = self.viewport
            delta = round((end - start) * steps * 0.15)
            self.set_viewport(start + delta, end + delta)
        else:
            self.zoom(0.8**steps, self.x_to_sample(event.position().x()))
        event.accept()
