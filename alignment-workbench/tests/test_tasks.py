from __future__ import annotations

import threading

from alignment_workbench.services.tasks import TaskManager


def test_replacement_task_rejects_stale_result(qtbot) -> None:
    manager = TaskManager()
    first_release = threading.Event()
    received: list[str] = []
    manager.completed.connect(lambda _category, _token, result: received.append(result))
    manager.submit(
        "catalog",
        lambda _cancel, _progress: (first_release.wait(2), "stale")[1],
        replace=True,
    )
    manager.submit("catalog", lambda _cancel, _progress: "current", replace=True)
    qtbot.waitUntil(lambda: received == ["current"])
    first_release.set()
    assert manager.wait()
    assert received == ["current"]
