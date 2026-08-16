from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from src.model.project import Project


class AnalysisSettingsPanel(QtWidgets.QWidget):
    settings_changed = QtCore.Signal()

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        form = QtWidgets.QFormLayout(self)
        settings = project.settings
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["linear", "mel"])
        self.mode.setCurrentText(settings.frequency_mode)
        self.fft = QtWidgets.QComboBox()
        self.fft.addItems(["256", "512", "1024", "2048", "4096"])
        self.fft.setCurrentText(str(settings.n_fft))
        self.window_ms = self._spin(settings.window_ms, 5, 100, " ms")
        self.hop_ms = self._spin(settings.hop_ms, 1, 50, " ms")
        self.dynamic = self._spin(settings.dynamic_range_db, 20, 140, " dB")
        self.fmax = self._spin(settings.fmax, 100, settings.analysis_rate / 2, " Hz")
        self.f0 = QtWidgets.QCheckBox()
        self.f0.setChecked(settings.show_f0)
        self.rms = QtWidgets.QCheckBox()
        self.rms.setChecked(settings.show_rms)
        self.formants = QtWidgets.QCheckBox()
        self.formants.setChecked(settings.show_formants)
        self.formants.setToolTip(
            "Experimental LPC estimates; recording conditions and speaker anatomy affect results"
        )
        form.addRow("Frequency mode", self.mode)
        form.addRow("FFT size", self.fft)
        form.addRow("Window", self.window_ms)
        form.addRow("Hop", self.hop_ms)
        form.addRow("Dynamic range", self.dynamic)
        form.addRow("Maximum frequency", self.fmax)
        form.addRow("F0 overlay", self.f0)
        form.addRow("RMS overlay", self.rms)
        form.addRow("F1-F3 overlay (experimental)", self.formants)
        apply_button = QtWidgets.QPushButton("Apply analysis settings")
        apply_button.clicked.connect(self.apply)
        form.addRow(apply_button)

    def _spin(self, value: float, low: float, high: float, suffix: str) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    @QtCore.Slot()
    def apply(self) -> None:
        settings = self.project.settings
        settings.frequency_mode = self.mode.currentText()
        settings.n_fft = int(self.fft.currentText())
        settings.window_ms = self.window_ms.value()
        settings.hop_ms = self.hop_ms.value()
        settings.dynamic_range_db = self.dynamic.value()
        settings.fmax = self.fmax.value()
        settings.show_f0 = self.f0.isChecked()
        settings.show_rms = self.rms.isChecked()
        settings.show_formants = self.formants.isChecked()
        self.settings_changed.emit()
