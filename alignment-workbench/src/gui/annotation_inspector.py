"""Time controls and selection presentation around the existing annotation edit form."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from gui.main_window import MainWindow


class AnnotationInspector(QtCore.QObject):
    """Bind seconds to the existing sample fields; the edit session stays authoritative."""

    def __init__(self, editor: MainWindow) -> None:
        super().__init__(editor)
        self.editor = editor
        self.start = QtWidgets.QDoubleSpinBox()
        self.end = QtWidgets.QDoubleSpinBox()
        for field in (self.start, self.end):
            field.setDecimals(6)
            field.setSuffix(" s")
        editor.editor_form.insertRow(4, "Start time", self.start)
        editor.editor_form.insertRow(5, "End time", self.end)
        editor.editor_form.setRowVisible(editor.start_sample, False)
        editor.editor_form.setRowVisible(editor.end_sample, False)
        self.start.valueChanged.connect(lambda value: self._write(value, False))
        self.end.valueChanged.connect(lambda value: self._write(value, True))
        editor.start_sample.valueChanged.connect(self.sync)
        editor.end_sample.valueChanged.connect(self.sync)
        editor.controller.session_changed.connect(self.sync)
        editor.controller.busy_changed.connect(self.sync)
        editor.geometry_combo.currentIndexChanged.connect(self.sync)
        self.revert = QtWidgets.QPushButton("Revert form")
        editor.editor_form.addRow(self.revert)
        self.revert.clicked.connect(self._revert)
        self.sync()

    def sync(self, *_: object) -> None:
        editor = self.editor
        session = editor.controller.session
        if session is None:
            return
        for seconds, samples in ((self.start, editor.start_sample), (self.end, editor.end_sample)):
            with QtCore.QSignalBlocker(seconds):
                seconds.setRange(
                    samples.minimum() / session.audio.asset.sample_rate_hz,
                    samples.maximum() / session.audio.asset.sample_rate_hz,
                )
                seconds.setValue(samples.value() / session.audio.asset.sample_rate_hz)
            seconds.setEnabled(not editor.controller.busy)
        self.end.setEnabled(
            not editor.controller.busy and editor.geometry_combo.currentData() != "point"
        )
        self.revert.setEnabled(not editor.controller.busy)

    def _write(self, seconds: float, end: bool) -> None:
        session = self.editor.controller.session
        if session:
            field = self.editor.end_sample if end else self.editor.start_sample
            field.setValue(round(seconds * session.audio.asset.sample_rate_hz))

    def _revert(self) -> None:
        editor = self.editor
        session = editor.controller.session
        if session and editor._selected_annotation_id:
            editor._populate_editor(session.get_annotation(editor._selected_annotation_id))
        else:
            editor._new_annotation()
