from __future__ import annotations

from uuid import UUID

from PySide6 import QtCore

from alignment_workbench.state.editor import EditorSession, RenderPlan, RenderSnapshot

_DETACHED_RENDER_COORDINATORS: set[RenderCoordinator] = set()


class RenderSignals(QtCore.QObject):
    completed = QtCore.Signal(object)
    finished = QtCore.Signal(object, int)


class RenderTask(QtCore.QRunnable):
    def __init__(self, plan: RenderPlan) -> None:
        super().__init__()
        self.plan: RenderPlan | None = plan
        self.signals = RenderSignals()

    @QtCore.Slot()
    def run(self) -> None:
        assert self.plan is not None
        track_id = self.plan.track_id
        version = self.plan.content_version
        try:
            self.signals.completed.emit(EditorSession.compose_render_plan(self.plan))
        finally:
            self.plan = None
            self.signals.finished.emit(track_id, version)


class RenderCoordinator(QtCore.QObject):
    ready = QtCore.Signal(object)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._latest: dict[UUID, int] = {}
        self._tasks: list[RenderTask] = []
        self._detached = False

    def render(self, plan: RenderPlan) -> None:
        self._latest[plan.track_id] = plan.content_version
        task = RenderTask(plan)
        task.signals.completed.connect(self._completed)
        task.signals.finished.connect(self._finished)
        self._tasks.append(task)
        self.pool.start(task)

    @QtCore.Slot(object)
    def _completed(self, snapshot: RenderSnapshot) -> None:
        if self._latest.get(snapshot.track_id) == snapshot.content_version:
            self.ready.emit(snapshot)

    @QtCore.Slot(object, int)
    def _finished(self, track_id: UUID, version: int) -> None:
        source = self.sender()
        self._tasks = [task for task in self._tasks if task.signals is not source]
        if self._latest.get(track_id) == version:
            self._latest.pop(track_id, None)
        if self._detached and not self._tasks:
            QtCore.QTimer.singleShot(0, self._release_detached)

    def _release_detached(self) -> None:
        _DETACHED_RENDER_COORDINATORS.discard(self)

    def cancel_all(self) -> None:
        self._latest.clear()

    def wait(self, milliseconds: int) -> bool:
        done = self.pool.waitForDone(milliseconds)
        application = QtCore.QCoreApplication.instance()
        if application is not None:
            for _ in range(8):
                application.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents)
                if not self._tasks:
                    break
        return done and not self._tasks

    def detach_running(self) -> None:
        self.cancel_all()
        self.ready.disconnect()
        self._detached = True
        application = QtCore.QCoreApplication.instance()
        self.setParent(application)
        if self._tasks:
            _DETACHED_RENDER_COORDINATORS.add(self)
