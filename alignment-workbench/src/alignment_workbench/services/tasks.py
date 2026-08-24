from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from PySide6 import QtCore


class TaskSignals(QtCore.QObject):
    completed = QtCore.Signal(object, object)
    failed = QtCore.Signal(object, str)
    progress = QtCore.Signal(object, str, float)


class BackgroundTask(QtCore.QRunnable):
    def __init__(
        self,
        request_id: UUID,
        operation: Callable[[threading.Event, Callable[[str, float], None]], Any],
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.operation = operation
        self.cancelled = threading.Event()
        self.signals = TaskSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self.operation(
                self.cancelled,
                lambda label, amount: self.signals.progress.emit(
                    self.request_id, label, max(0.0, min(1.0, amount))
                ),
            )
            if not self.cancelled.is_set():
                self.signals.completed.emit(self.request_id, result)
        except Exception as exc:
            if not self.cancelled.is_set():
                self.signals.failed.emit(self.request_id, str(exc))


class TaskManager(QtCore.QObject):
    completed = QtCore.Signal(str, object, object)
    failed = QtCore.Signal(str, object, str)
    progress = QtCore.Signal(str, object, str, float)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self._tasks: dict[str, dict[UUID, BackgroundTask]] = {}
        self._latest: dict[str, UUID] = {}

    def submit(
        self,
        category: str,
        operation: Callable[[threading.Event, Callable[[str, float], None]], Any],
        *,
        replace: bool = False,
    ) -> UUID:
        if replace:
            self.cancel(category)
        request_id = uuid4()
        task = BackgroundTask(request_id, operation)
        task.signals.completed.connect(
            lambda token, result, name=category: self._completed(name, token, result)
        )
        task.signals.failed.connect(
            lambda token, message, name=category: self._failed(name, token, message)
        )
        task.signals.progress.connect(
            lambda token, label, amount, name=category: self.progress.emit(
                name, token, label, amount
            )
        )
        self._tasks.setdefault(category, {})[request_id] = task
        self._latest[category] = request_id
        self.pool.start(task)
        return request_id

    def cancel(self, category: str) -> None:
        for task in self._tasks.get(category, {}).values():
            task.cancelled.set()

    def cancel_all(self) -> None:
        for category in tuple(self._tasks):
            self.cancel(category)

    def is_latest(self, category: str, request_id: UUID) -> bool:
        return self._latest.get(category) == request_id

    def _completed(self, category: str, request_id: UUID, result: object) -> None:
        self._discard(category, request_id)
        if self.is_latest(category, request_id):
            self.completed.emit(category, request_id, result)

    def _failed(self, category: str, request_id: UUID, message: str) -> None:
        self._discard(category, request_id)
        if self.is_latest(category, request_id):
            self.failed.emit(category, request_id, message)

    def _discard(self, category: str, request_id: UUID) -> None:
        self._tasks.get(category, {}).pop(request_id, None)

    def wait(self, milliseconds: int = 10_000) -> bool:
        complete = self.pool.waitForDone(milliseconds)
        if complete:
            self._tasks.clear()
        return complete
