from __future__ import annotations

from uuid import UUID

import pyqtgraph as pg
from PySide6 import QtCore

from src.analysis.cache import AnalysisCache
from src.analysis.waveform_lod import WaveformEnvelope, create_envelope
from src.model.project import Project
from src.model.track import Track

from .linked_view import InteractivePlot, add_timeline_items
from .track_workspace import TrackWorkspace


class WaveformSignals(QtCore.QObject):
    completed = QtCore.Signal(object, object, int)
    failed = QtCore.Signal(str)


class WaveformTask(QtCore.QRunnable):
    def __init__(self, track: Track, cache: AnalysisCache, generation: int) -> None:
        super().__init__()
        self.track = track
        self.cache = cache
        self.generation = generation
        self.signals = WaveformSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            key = self.cache.key(
                self.track.id,
                "waveform",
                self.track.trim_start_frame,
                self.track.trim_end_frame,
            )
            envelope = self.cache.get_or_compute(
                key,
                lambda: create_envelope(self.track.playback_view, self.track.playback_rate),
            )
            self.signals.completed.emit(self.track.id, envelope, self.generation)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class WaveformWorkspace(TrackWorkspace):
    analysis_failed = QtCore.Signal(str)

    def __init__(self, project: Project, cache: AnalysisCache, parent=None) -> None:
        self.cache = cache
        self._generation = 0
        self._tasks: list[WaveformTask] = []
        super().__init__(project, parent)
        self._thread_pool = QtCore.QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)

    def rebuild(self) -> None:
        self._generation += 1
        super().rebuild()

    def populate_plot(self, plot: InteractivePlot, track: Track) -> None:
        plot.setYRange(-1.05 * track.amplitude_scale, 1.05 * track.amplitude_scale, padding=0)
        plot.setLabel("left", "Amplitude")
        key = self.cache.key(track.id, "waveform", track.trim_start_frame, track.trim_end_frame)
        envelope = self.cache.get(key)
        if envelope is not None:
            self._render_envelope(plot, track, envelope)
            return
        plot.addItem(pg.TextItem("Preparing waveform...", color="#aeb4bc", anchor=(0, 0)))
        task = WaveformTask(track, self.cache, self._generation)
        task.signals.completed.connect(self._analysis_ready)
        task.signals.failed.connect(self._analysis_failed)
        self._tasks.append(task)
        self._thread_pool.start(task)

    def _render_envelope(
        self, plot: InteractivePlot, track: Track, envelope: WaveformEnvelope
    ) -> None:
        minimum = plot.plot(
            envelope.times,
            envelope.minimum * track.amplitude_scale,
            pen=pg.mkPen(track.color, width=1),
        )
        maximum = plot.plot(
            envelope.times,
            envelope.maximum * track.amplitude_scale,
            pen=pg.mkPen(track.color, width=1),
        )
        plot.addItem(pg.FillBetweenItem(minimum, maximum, brush=pg.mkBrush(track.color + "55")))

    @QtCore.Slot(object, object, int)
    def _analysis_ready(self, track_id: UUID, envelope: WaveformEnvelope, generation: int) -> None:
        self._discard_completed_task()
        if generation != self._generation:
            return
        plot = self.plots.get(track_id)
        if plot is None:
            return
        track = self.project.track_by_id(track_id)
        plot.clear()
        self._render_envelope(plot, track, envelope)
        playhead, region = add_timeline_items(plot)
        self.playheads[track_id], self.regions[track_id] = playhead, region
        self.update_timeline()

    @QtCore.Slot(str)
    def _analysis_failed(self, message: str) -> None:
        self._discard_completed_task()
        self.analysis_failed.emit(message)

    def _discard_completed_task(self) -> None:
        source = self.sender()
        self._tasks = [task for task in self._tasks if task.signals is not source]

    def wait_for_tasks(self, milliseconds: int = 5_000) -> bool:
        finished = self._thread_pool.waitForDone(milliseconds)
        if finished:
            self._tasks.clear()
        return finished
