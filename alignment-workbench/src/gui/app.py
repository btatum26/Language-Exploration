"""Qt lifecycle entry point composed with the real workbench application."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Protocol, cast

from PySide6 import QtWidgets

from application import WorkbenchApplication, WorkbenchError, WorkbenchSettings
from gui.controller import ApplicationClient
from gui.main_window import MainWindow


class Window(Protocol):
    def show(self) -> None: ...

    def shutdown(self) -> None: ...


class ApplicationLifecycle(Protocol):
    @property
    def api(self) -> ApplicationClient: ...

    def start(self) -> object: ...

    def shutdown(self) -> None: ...


def run_started_gui(
    qt_application: QtWidgets.QApplication,
    application: ApplicationLifecycle,
    *,
    window_factory: Callable[[ApplicationClient], Window] = MainWindow,
    event_loop: Callable[[], int] | None = None,
) -> int:
    """Start infrastructure, inject the API into one window, and always shut down."""

    window: Window | None = None
    try:
        application.start()
        window = window_factory(application.api)
        window.show()
        return event_loop() if event_loop is not None else qt_application.exec()
    finally:
        if window is not None:
            window.shutdown()
        application.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    qt_application = cast(QtWidgets.QApplication | None, QtWidgets.QApplication.instance())
    if qt_application is None:
        qt_application = QtWidgets.QApplication(arguments)
    qt_application.setApplicationName("Alignment Workbench")
    try:
        settings = WorkbenchSettings.from_environment()
        application = WorkbenchApplication(settings)
        return run_started_gui(qt_application, application)
    except (WorkbenchError, ValueError) as exc:
        QtWidgets.QMessageBox.critical(
            None,
            "Alignment Workbench could not start",
            str(exc),
        )
        return 2
    except Exception:
        QtWidgets.QMessageBox.critical(
            None,
            "Alignment Workbench could not start",
            "An unexpected startup failure occurred. Check the terminal diagnostics.",
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
