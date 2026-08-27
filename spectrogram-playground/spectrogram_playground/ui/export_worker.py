from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore

from spectrogram_playground.analysis.cache import AnalysisCache
from spectrogram_playground.exporting import export_acoustic_csv
from spectrogram_playground.model.project import Project


class ExportSignals(QtCore.QObject):
    completed = QtCore.Signal(str)
    failed = QtCore.Signal(str)


class AcousticCsvExportTask(QtCore.QRunnable):
    """Compute acoustic tracks and write CSV away from the Qt thread."""

    def __init__(self, project: Project, cache: AnalysisCache, path: str | Path) -> None:
        super().__init__()
        self.project = project
        self.cache = cache
        self.path = str(path)
        self.signals = ExportSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            export_acoustic_csv(self.project, self.cache, self.path)
            self.signals.completed.emit(self.path)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
