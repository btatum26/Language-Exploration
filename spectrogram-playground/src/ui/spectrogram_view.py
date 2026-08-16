from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore

from src.analysis.cache import AnalysisCache
from src.analysis.formants import estimate_formants
from src.analysis.pitch import estimate_acoustic_tracks
from src.analysis.spectrogram import calculate_spectrogram
from src.model.project import Project
from src.model.track import Track

from .linked_view import InteractivePlot, add_timeline_items
from .track_workspace import TrackWorkspace


class WorkerSignals(QtCore.QObject):
    completed = QtCore.Signal(object, object, object)
    failed = QtCore.Signal(object, str)


class SpectrogramTask(QtCore.QRunnable):
    def __init__(
        self, track: Track, settings: object, cache: AnalysisCache, generation: int
    ) -> None:
        super().__init__()
        self.track, self.settings, self.cache = track, settings, cache
        self.generation = generation
        self.signals = WorkerSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            settings = self.settings
            spec_key = self.cache.key(
                self.track.id,
                "spectrogram",
                settings.frequency_mode,
                settings.window_ms,
                settings.hop_ms,
                settings.n_fft,
                settings.dynamic_range_db,
                settings.n_mels,
                settings.fmin,
                settings.fmax,
                self.track.trim_start_frame,
                self.track.trim_end_frame,
            )
            result = self.cache.get_or_compute(
                spec_key,
                lambda: calculate_spectrogram(
                    self.track.analysis_view,
                    self.track.analysis_rate,
                    mode=settings.frequency_mode,
                    window_ms=settings.window_ms,
                    hop_ms=settings.hop_ms,
                    n_fft=settings.n_fft,
                    dynamic_range_db=settings.dynamic_range_db,
                    n_mels=settings.n_mels,
                    fmin=settings.fmin,
                    fmax=settings.fmax,
                ),
            )
            acoustic = None
            if settings.show_f0 or settings.show_rms:
                acoustic_key = self.cache.key(
                    self.track.id,
                    "acoustic",
                    settings.window_ms,
                    settings.hop_ms,
                    self.track.trim_start_frame,
                    self.track.trim_end_frame,
                )
                acoustic = self.cache.get_or_compute(
                    acoustic_key,
                    lambda: estimate_acoustic_tracks(
                        self.track.analysis_view,
                        self.track.analysis_rate,
                        window_ms=settings.window_ms,
                        hop_ms=settings.hop_ms,
                    ),
                )
            formants = None
            if settings.show_formants:
                formant_key = self.cache.key(
                    self.track.id,
                    "formants",
                    settings.window_ms,
                    settings.hop_ms,
                    self.track.trim_start_frame,
                    self.track.trim_end_frame,
                )
                formants = self.cache.get_or_compute(
                    formant_key,
                    lambda: estimate_formants(
                        self.track.analysis_view,
                        self.track.analysis_rate,
                        window_ms=settings.window_ms,
                        hop_ms=settings.hop_ms,
                    ),
                )
            overlays = {"acoustic": acoustic, "formants": formants}
            self.signals.completed.emit(self.track.id, (result, self.generation), overlays)
        except Exception as exc:
            self.signals.failed.emit(self.track.id, str(exc))


class SpectrogramWorkspace(TrackWorkspace):
    analysis_failed = QtCore.Signal(str)

    def __init__(self, project: Project, cache: AnalysisCache, parent=None) -> None:
        self.cache = cache
        self._tasks: list[SpectrogramTask] = []
        self._images: dict[UUID, pg.ImageItem] = {}
        self._generation = 0
        super().__init__(project, parent)
        self._thread_pool = QtCore.QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)

    def rebuild(self) -> None:
        self._generation += 1
        super().rebuild()

    def clear_lanes(self) -> None:
        super().clear_lanes()
        self._images.clear()

    def populate_plot(self, plot: InteractivePlot, track: Track) -> None:
        plot.setLabel("left", "Frequency", units="Hz")
        plot.setYRange(self.project.settings.fmin, self.project.settings.fmax, padding=0)
        plot.addItem(pg.TextItem("Computing…", color="#aeb4bc", anchor=(0, 0)))
        task = SpectrogramTask(track, replace(self.project.settings), self.cache, self._generation)
        task.signals.completed.connect(self._analysis_ready)
        task.signals.failed.connect(self._analysis_failed)
        self._tasks.append(task)
        self._thread_pool.start(task)

    @QtCore.Slot(object, object, object)
    def _analysis_ready(self, track_id: UUID, payload: object, overlays: object) -> None:
        result, generation = payload
        if generation != self._generation:
            self._discard_completed_task()
            return
        plot = self.plots.get(track_id)
        if plot is None:
            return
        plot.clear()
        image = pg.ImageItem(axisOrder="row-major")
        image.setImage(
            result.decibels, autoLevels=False, levels=(-self.project.settings.dynamic_range_db, 0)
        )
        image.setLookupTable(pg.colormap.get("inferno").getLookupTable(nPts=256))
        duration = max(0.001, float(result.times[-1]) if len(result.times) else 0.001)
        if result.mode == "mel":
            y_max = len(result.frequencies)
            plot.setLabel("left", "Mel band")
            image.setRect(QtCore.QRectF(0, 0, duration, y_max))
            plot.setYRange(0, y_max, padding=0)
        else:
            y_min, y_max = float(result.frequencies[0]), float(result.frequencies[-1])
            image.setRect(QtCore.QRectF(0, y_min, duration, y_max - y_min))
            plot.setYRange(
                self.project.settings.fmin,
                min(self.project.settings.fmax, y_max),
                padding=0,
            )
        plot.addItem(image)
        self._images[track_id] = image
        acoustic = overlays["acoustic"]
        formants = overlays["formants"]
        if acoustic is not None:
            track = self.project.track_by_id(track_id)
            if self.project.settings.show_f0:
                f0 = acoustic.f0_hz
                y = (
                    np.interp(f0, result.frequencies, np.arange(len(result.frequencies)))
                    if result.mode == "mel"
                    else f0
                )
                plot.plot(
                    acoustic.times, y, pen=None, symbol="o", symbolSize=3, symbolBrush=track.color
                )
            if self.project.settings.show_rms:
                ceiling = (
                    len(result.frequencies) if result.mode == "mel" else self.project.settings.fmax
                )
                plot.plot(acoustic.times, acoustic.rms * ceiling * 0.25, pen=pg.mkPen("#f2cc8f"))
        if formants is not None:
            track_colors = ("#5ee7ff", "#9cff57", "#ff78d1")
            plot.addLegend(offset=(8, 8))
            for index, (label, color) in enumerate(
                zip(("F1", "F2", "F3"), track_colors, strict=True)
            ):
                values = formants.frequencies_hz[index]
                y = (
                    np.interp(
                        values,
                        result.frequencies,
                        np.arange(len(result.frequencies)),
                    )
                    if result.mode == "mel"
                    else values
                )
                plot.plot(
                    formants.times,
                    y,
                    pen=pg.mkPen(color, width=1.5),
                    name=f"{label} experimental",
                    connect="finite",
                )
        playhead, region = add_timeline_items(plot)
        self.playheads[track_id], self.regions[track_id] = playhead, region
        self.update_timeline()
        self._discard_completed_task()

    @QtCore.Slot(object, str)
    def _analysis_failed(self, track_id: UUID, message: str) -> None:
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
