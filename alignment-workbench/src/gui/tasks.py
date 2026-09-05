"""Small Qt worker boundary for synchronous application calls."""

from __future__ import annotations

from collections.abc import Callable
from itertools import count
from typing import Any, Protocol, TypeVar

from PySide6 import QtCore

T = TypeVar("T")


class TaskSubmitter(Protocol):
    def submit(
        self,
        operation: Callable[[], T],
        on_success: Callable[[T], None],
        on_failure: Callable[[BaseException], None],
    ) -> None: ...


class _TaskSignals(QtCore.QObject):
    succeeded = QtCore.Signal(int, object)
    failed = QtCore.Signal(int, object)


class _FunctionTask(QtCore.QRunnable):
    def __init__(self, token: int, operation: Callable[[], object]) -> None:
        super().__init__()
        self.token = token
        self.operation = operation
        self.signals = _TaskSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except BaseException as exc:
            self.signals.failed.emit(self.token, exc)
        else:
            self.signals.succeeded.emit(self.token, result)


class TaskRunner(QtCore.QObject):
    """Run callables off the Qt thread and return every result on it."""

    def __init__(self, parent: QtCore.QObject | None = None, *, max_threads: int = 2) -> None:
        super().__init__(parent)
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(max_threads)
        self._tokens = count(1)
        self._tasks: dict[int, _FunctionTask] = {}
        self._callbacks: dict[
            int,
            tuple[Callable[[Any], None], Callable[[BaseException], None]],
        ] = {}

    def submit(
        self,
        operation: Callable[[], T],
        on_success: Callable[[T], None],
        on_failure: Callable[[BaseException], None],
    ) -> None:
        token = next(self._tokens)
        task = _FunctionTask(token, operation)
        self._tasks[token] = task
        self._callbacks[token] = (on_success, on_failure)
        task.signals.succeeded.connect(self._succeeded)
        task.signals.failed.connect(self._failed)
        self._pool.start(task)

    @QtCore.Slot(int, object)
    def _succeeded(self, token: int, result: object) -> None:
        callbacks = self._callbacks.pop(token, None)
        self._tasks.pop(token, None)
        if callbacks is not None:
            callbacks[0](result)

    @QtCore.Slot(int, object)
    def _failed(self, token: int, error: object) -> None:
        callbacks = self._callbacks.pop(token, None)
        self._tasks.pop(token, None)
        if callbacks is not None:
            if isinstance(error, BaseException):
                callbacks[1](error)
            else:
                callbacks[1](RuntimeError("background task failed without an exception"))

    def shutdown(self, milliseconds: int = 5_000) -> bool:
        return self._pool.waitForDone(milliseconds)
