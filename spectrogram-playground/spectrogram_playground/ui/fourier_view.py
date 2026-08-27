from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from spectrogram_playground.analysis.cache import AnalysisCache
from spectrogram_playground.analysis.spectrum import SpectrumResult, calculate_spectrum
from spectrogram_playground.model.project import Project


@dataclass(frozen=True, slots=True)
class SpectrumJob:
    track_id: UUID
    name: str
    color: str
    samples: np.ndarray
    sample_rate: int
    first: int
    last: int
    window: str
    trim_start_frame: int
    trim_end_frame: int


class FourierSignals(QtCore.QObject):
    completed = QtCore.Signal(object, int)
    failed = QtCore.Signal(str, int)


class FourierTask(QtCore.QRunnable):
    """Calculate all visible-track spectra without touching Qt widgets."""

    def __init__(
        self,
        jobs: list[SpectrumJob],
        cache: AnalysisCache,
        generation: int,
    ) -> None:
        super().__init__()
        self.jobs = jobs
        self.cache = cache
        self.generation = generation
        self.signals = FourierSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            results: list[tuple[str, str, SpectrumResult]] = []
            for job in self.jobs:
                key = self.cache.key(
                    job.track_id,
                    "spectrum",
                    job.first,
                    job.last,
                    job.window,
                    job.trim_start_frame,
                    job.trim_end_frame,
                )
                result = self.cache.get_or_compute(
                    key,
                    lambda job=job: calculate_spectrum(
                        job.samples[job.first : job.last],
                        job.sample_rate,
                        window=job.window,
                    ),
                )
                results.append((job.name, job.color, result))
            self.signals.completed.emit(results, self.generation)
        except Exception as exc:
            self.signals.failed.emit(str(exc), self.generation)


class FourierView(QtWidgets.QWidget):
    analysis_started = QtCore.Signal()
    analysis_finished = QtCore.Signal()
    analysis_failed = QtCore.Signal(str)

    def __init__(self, project: Project, cache: AnalysisCache, parent=None) -> None:
        super().__init__(parent)
        self.project, self.cache = project, cache
        self.last_interval = (0.0, 0.0)
        self._generation = 0
        self._tasks: list[FourierTask] = []
        self._thread_pool = QtCore.QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.layout_mode = QtWidgets.QComboBox()
        self.layout_mode.addItems(["Overlay", "Stacked"])
        self.scale = QtWidgets.QComboBox()
        self.scale.addItems(["Decibel", "Magnitude", "Power"])
        self.log_frequency = QtWidgets.QCheckBox("Log frequency")
        self.window = QtWidgets.QComboBox()
        self.window.addItems(["hann", "hamming", "blackman", "boxcar"])
        self.window.setCurrentText(project.settings.spectrum_window)
        for label, widget in (("Layout", self.layout_mode), ("Scale", self.scale)):
            controls.addWidget(QtWidgets.QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(self.log_frequency)
        controls.addWidget(QtWidgets.QLabel("Window"))
        controls.addWidget(self.window)
        controls.addStretch()
        layout.addLayout(controls)
        self.region_label = QtWidgets.QLabel()
        layout.addWidget(self.region_label)
        self.canvas = pg.GraphicsLayoutWidget()
        self.canvas.setBackground("#15171a")
        layout.addWidget(self.canvas, 1)
        self.layout_mode.currentTextChanged.connect(self.refresh)
        self.scale.currentTextChanged.connect(self.refresh)
        self.log_frequency.toggled.connect(self.refresh)
        self.window.currentTextChanged.connect(self._window_changed)

    def _window_changed(self, value: str) -> None:
        self.project.settings.spectrum_window = value
        self.refresh()

    def analyzed_interval(self) -> tuple[float, float]:
        if self.project.selection.active:
            return self.project.selection.start, self.project.selection.end  # type: ignore[return-value]
        center = self.project.transport.frame_position / self.project.playback_rate
        half = self.project.settings.default_spectrum_window_seconds / 2
        start = max(0.0, center - half)
        end = min(self.project.duration, center + half)
        if end <= start:
            end = min(
                self.project.duration,
                start + self.project.settings.default_spectrum_window_seconds,
            )
        return start, end

    @QtCore.Slot()
    def refresh(self) -> None:
        self._generation += 1
        generation = self._generation
        self.canvas.clear()
        start, end = self.analyzed_interval()
        self.last_interval = start, end
        visible = [track for track in self.project.tracks if track.visible]
        if not visible or end <= start:
            self.region_label.setText("No analyzable audio region")
            return
        jobs = []
        for track in visible:
            analysis = track.analysis_view
            first = min(len(analysis), max(0, round(start * track.analysis_rate)))
            last = min(
                len(analysis),
                max(first + 1, round(end * track.analysis_rate)),
            )
            jobs.append(
                SpectrumJob(
                    track.id,
                    track.name,
                    track.color,
                    analysis,
                    track.analysis_rate,
                    first,
                    last,
                    self.project.settings.spectrum_window,
                    track.trim_start_frame,
                    track.trim_end_frame,
                )
            )
        source = "selection" if self.project.selection.active else "playhead-centered window"
        self.region_label.setText(f"Analyzing {start:.3f}-{end:.3f} s ({source})...")
        task = FourierTask(jobs, self.cache, generation)
        task.signals.completed.connect(self._analysis_ready)
        task.signals.failed.connect(self._analysis_failed)
        self._tasks.append(task)
        self.analysis_started.emit()
        self._thread_pool.start(task)

    @QtCore.Slot(object, int)
    def _analysis_ready(
        self, results: list[tuple[str, str, SpectrumResult]], generation: int
    ) -> None:
        self._discard_completed_task()
        if generation != self._generation:
            return
        self._render_results(results)
        self.analysis_finished.emit()

    @QtCore.Slot(str, int)
    def _analysis_failed(self, message: str, generation: int) -> None:
        self._discard_completed_task()
        if generation == self._generation:
            self.region_label.setText("Fourier analysis failed")
            self.analysis_failed.emit(message)

    def _render_results(self, results: list[tuple[str, str, SpectrumResult]]) -> None:
        self.canvas.clear()
        stacked = self.layout_mode.currentText() == "Stacked"
        scale = self.scale.currentText()
        method = ""
        shared_plot = None
        plots: list[pg.PlotItem] = []
        value_ranges: list[tuple[float, float]] = []
        for index, (name, color, result) in enumerate(results):
            method = result.method
            if stacked or shared_plot is None:
                plot = self.canvas.addPlot(row=index if stacked else 0, col=0)
                plot.showGrid(x=True, y=True, alpha=0.15)
                if stacked:
                    plot.setTitle(name, color=color, size="9pt")
                    if shared_plot is not None:
                        plot.setXLink(shared_plot)
                shared_plot = shared_plot or plot
                plots.append(plot)
            else:
                plot = shared_plot
            values = (
                result.decibels
                if scale == "Decibel"
                else (result.power if scale == "Power" else result.magnitude)
            )
            finite = values[np.isfinite(values)]
            if finite.size:
                value_ranges.append((float(finite.min()), float(finite.max())))
            if not stacked and index == 0:
                plot.addLegend()
            plot.plot(result.frequencies, values, pen=pg.mkPen(color, width=2), name=name)
            plot.setLogMode(x=self.log_frequency.isChecked(), y=False)
            plot.setLabel("bottom", "Frequency", units="Hz")
            plot.setLabel("left", scale)
        if value_ranges:
            lower = min(item[0] for item in value_ranges)
            upper = max(item[1] for item in value_ranges)
            padding = max(1e-9, (upper - lower) * 0.05)
            for plot in plots:
                plot.setYRange(lower - padding, upper + padding, padding=0)
        start, end = self.last_interval
        source = "selection" if self.project.selection.active else "playhead-centered window"
        self.region_label.setText(f"{start:.3f}-{end:.3f} s - {source} - {method}")

    def _discard_completed_task(self) -> None:
        source = self.sender()
        self._tasks = [task for task in self._tasks if task.signals is not source]

    def wait_for_tasks(self, milliseconds: int = 5_000) -> bool:
        finished = self._thread_pool.waitForDone(milliseconds)
        if finished:
            self._tasks.clear()
        return finished
