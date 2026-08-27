from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

from PySide6 import QtCore

_DETACHED_TASK_MANAGERS: set[TaskManager] = set()


class TaskSignals(QtCore.QObject):
    completed = QtCore.Signal(object, object)
    failed = QtCore.Signal(object, str)
    progress = QtCore.Signal(object, str, float)
    finished = QtCore.Signal(object)


class BackgroundTask(QtCore.QRunnable):
    def __init__(
        self,
        request_id: UUID,
        operation: Callable[[threading.Event, Callable[[str, float], None]], Any],
        *,
        deliver_after_cancel: bool,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.operation = operation
        self.deliver_after_cancel = deliver_after_cancel
        self.cancelled = threading.Event()
        self.signals = TaskSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:

            def report(label: str, amount: float) -> None:
                if not self.cancelled.is_set():
                    self.signals.progress.emit(self.request_id, label, max(0.0, min(1.0, amount)))

            result = self.operation(self.cancelled, report)
            if self.deliver_after_cancel or not self.cancelled.is_set():
                self.signals.completed.emit(self.request_id, result)
        except Exception as exc:
            if self.deliver_after_cancel or not self.cancelled.is_set():
                self.signals.failed.emit(self.request_id, str(exc))
        finally:
            self.operation = lambda _cancel, _progress: None
            self.signals.finished.emit(self.request_id)


class DuplicateTaskError(RuntimeError):
    """Raised when a durable mutation is submitted more than once."""


class TaskManager(QtCore.QObject):
    completed = QtCore.Signal(str, object, object)
    failed = QtCore.Signal(str, object, str)
    progress = QtCore.Signal(str, object, str, float)
    finished = QtCore.Signal(str, object)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self._tasks: dict[str, dict[UUID, BackgroundTask]] = {}
        self._latest: dict[str, UUID] = {}
        self._detached = False

    def submit(
        self,
        category: str,
        operation: Callable[[threading.Event, Callable[[str, float], None]], Any],
        *,
        replace: bool = False,
        mutation: bool = False,
    ) -> UUID:
        if replace and mutation:
            raise ValueError("mutation tasks cannot use replaceable result delivery")
        if replace:
            self.cancel(category)
        elif mutation and self._tasks.get(category):
            raise DuplicateTaskError(f"{category} is already in progress")
        request_id = uuid4()
        task = BackgroundTask(request_id, operation, deliver_after_cancel=mutation)
        task.signals.completed.connect(
            lambda token, result, name=category: self._completed(name, token, result)
        )
        task.signals.failed.connect(
            lambda token, message, name=category: self._failed(name, token, message)
        )
        task.signals.progress.connect(
            lambda token, label, amount, name=category: self._progress(name, token, label, amount)
        )
        task.signals.finished.connect(lambda token, name=category: self._finished(name, token))
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
        if not self._detached and self.is_latest(category, request_id):
            self.completed.emit(category, request_id, result)

    def _failed(self, category: str, request_id: UUID, message: str) -> None:
        if not self._detached and self.is_latest(category, request_id):
            self.failed.emit(category, request_id, message)

    def _progress(self, category: str, request_id: UUID, label: str, amount: float) -> None:
        task = self._tasks.get(category, {}).get(request_id)
        if (
            not self._detached
            and task is not None
            and not task.cancelled.is_set()
            and self.is_latest(category, request_id)
        ):
            self.progress.emit(category, request_id, label, amount)

    def _finished(self, category: str, request_id: UUID) -> None:
        QtCore.QTimer.singleShot(0, lambda: self._finalize(category, request_id))

    def _finalize(self, category: str, request_id: UUID) -> None:
        self._discard(category, request_id)
        if self._latest.get(category) == request_id:
            self._latest.pop(category, None)
        if not self._detached:
            self.finished.emit(category, request_id)
        if self._detached and not self._tasks:
            QtCore.QTimer.singleShot(0, self._release_detached)

    def _release_detached(self) -> None:
        _DETACHED_TASK_MANAGERS.discard(self)

    def _discard(self, category: str, request_id: UUID) -> None:
        tasks = self._tasks.get(category)
        if tasks is None:
            return
        tasks.pop(request_id, None)
        if not tasks:
            self._tasks.pop(category, None)

    def wait(self, milliseconds: int = 10_000) -> bool:
        done = self.pool.waitForDone(milliseconds)
        application = QtCore.QCoreApplication.instance()
        if application is not None:
            for _ in range(8):
                application.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents)
                if not self._tasks:
                    break
        return done and not self._tasks

    @property
    def active_count(self) -> int:
        return sum(len(tasks) for tasks in self._tasks.values())

    def detach_running(self) -> None:
        """Keep signal-owning tasks alive after their window has closed."""
        self._detached = True
        application = QtCore.QCoreApplication.instance()
        self.setParent(application)
        if self._tasks:
            _DETACHED_TASK_MANAGERS.add(self)
