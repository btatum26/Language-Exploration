from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import numpy as np
from PySide6 import QtCore
from src.analysis.cache import AnalysisCache
from src.analysis.spectrogram import SpectrogramResult, calculate_spectrogram
from src.analysis.waveform_lod import WaveformEnvelope, create_envelope


@dataclass(frozen=True, slots=True)
class TrackAnalysis:
    waveform: WaveformEnvelope
    spectrogram: SpectrogramResult


class AnalysisSignals(QtCore.QObject):
    completed = QtCore.Signal(object, int, object)
    failed = QtCore.Signal(object, int, str)


class AnalysisTask(QtCore.QRunnable):
    def __init__(
        self,
        track_id: UUID,
        generation: int,
        samples: np.ndarray,
        sample_rate: int,
        cache: AnalysisCache,
    ) -> None:
        super().__init__()
        self.track_id = track_id
        self.generation = generation
        self.samples = samples
        self.sample_rate = sample_rate
        self.cache = cache
        self.signals = AnalysisSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            wave_key = self.cache.key(self.track_id, "workbench-waveform", len(self.samples))
            spec_key = self.cache.key(self.track_id, "workbench-spectrogram", len(self.samples))
            waveform = self.cache.get_or_compute(
                wave_key, lambda: create_envelope(self.samples, self.sample_rate)
            )
            spectrogram = self.cache.get_or_compute(
                spec_key,
                lambda: calculate_spectrogram(
                    self.samples,
                    self.sample_rate,
                    mode="linear",
                    window_ms=25,
                    hop_ms=10,
                    n_fft=1024,
                    fmax=min(8_000, self.sample_rate / 2),
                ),
            )
            self.signals.completed.emit(
                self.track_id, self.generation, TrackAnalysis(waveform, spectrogram)
            )
        except Exception as exc:
            self.signals.failed.emit(self.track_id, self.generation, str(exc))


class AnalysisCoordinator(QtCore.QObject):
    completed = QtCore.Signal(object, int, object)
    failed = QtCore.Signal(object, int, str)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.cache = AnalysisCache()
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._tasks: list[AnalysisTask] = []

    def analyze(
        self,
        track_id: UUID,
        generation: int,
        samples: np.ndarray,
        sample_rate: int,
    ) -> None:
        task = AnalysisTask(track_id, generation, samples, sample_rate, self.cache)
        task.signals.completed.connect(self._completed)
        task.signals.failed.connect(self._failed)
        self._tasks.append(task)
        self.pool.start(task)

    @QtCore.Slot(object, int, object)
    def _completed(self, track_id: UUID, generation: int, result: TrackAnalysis) -> None:
        self._discard_sender()
        self.completed.emit(track_id, generation, result)

    @QtCore.Slot(object, int, str)
    def _failed(self, track_id: UUID, generation: int, message: str) -> None:
        self._discard_sender()
        self.failed.emit(track_id, generation, message)

    def _discard_sender(self) -> None:
        source = self.sender()
        self._tasks = [task for task in self._tasks if task.signals is not source]

    def invalidate(self, track_id: UUID) -> None:
        self.cache.invalidate_track(track_id)

    def wait(self, milliseconds: int = 5_000) -> bool:
        complete = self.pool.waitForDone(milliseconds)
        if complete:
            self._tasks.clear()
        return complete
