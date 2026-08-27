from __future__ import annotations

import sys
from pathlib import Path

import pyqtgraph as pg
from PySide6 import QtWidgets

from spectrogram_playground.ui.main_window import MainWindow


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Spectrogram Playground")
    app.setOrganizationName("Spectrogram Playground")
    pg.setConfigOptions(antialias=False, background="#15171a", foreground="#d7dce2")
    stylesheet = Path(__file__).with_name("resources") / "styles.qss"
    app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))
    window = MainWindow()
    window.show()
    return app.exec()
