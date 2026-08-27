from __future__ import annotations

import threading
import time

import pytest

from alignment_workbench.services.tasks import DuplicateTaskError, TaskManager


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
    assert manager.active_count == 0
    assert manager._latest == {}


def test_cancelled_task_is_removed_even_without_a_result(qtbot) -> None:
    manager = TaskManager()
    release = threading.Event()
    manager.submit("catalog", lambda _cancel, _progress: release.wait(2), replace=True)
    manager.cancel("catalog")
    release.set()
    qtbot.waitUntil(lambda: manager.active_count == 0)
    assert manager._latest == {}


def test_repeated_replacement_searches_do_not_accumulate(qtbot) -> None:
    manager = TaskManager()
    received: list[int] = []
    manager.completed.connect(lambda _category, _token, result: received.append(result))
    for value in range(20):
        manager.submit("catalog", lambda _cancel, _progress, item=value: item, replace=True)
    qtbot.waitUntil(lambda: received == [19])
    qtbot.waitUntil(lambda: manager.active_count == 0)
    assert manager._tasks == {}
    assert manager._latest == {}


def test_mutation_rejects_concurrent_duplicate(qtbot) -> None:
    manager = TaskManager()
    release = threading.Event()
    manager.submit("revision", lambda _cancel, _progress: release.wait(2), mutation=True)
    with pytest.raises(DuplicateTaskError, match="already in progress"):
        manager.submit("revision", lambda _cancel, _progress: None, mutation=True)
    release.set()
    qtbot.waitUntil(lambda: manager.active_count == 0)


def test_progress_stops_after_cancel(qtbot) -> None:
    manager = TaskManager()
    release = threading.Event()
    progress: list[float] = []
    manager.progress.connect(lambda _category, _token, _label, value: progress.append(value))

    def operation(_cancel, report):
        report("before", 0.25)
        release.wait(2)
        report("after", 0.75)

    manager.submit("catalog", operation, replace=True)
    qtbot.waitUntil(lambda: progress == [0.25])
    manager.cancel("catalog")
    release.set()
    qtbot.waitUntil(lambda: manager.active_count == 0)
    time.sleep(0.01)
    assert progress == [0.25]


def test_mutation_results_keep_their_request_context(qtbot) -> None:
    manager = TaskManager()
    received = []
    manager.completed.connect(
        lambda category, token, result: received.append((category, token, result))
    )

    first = manager.submit("speaker-create", lambda _cancel, _progress: "speaker", mutation=True)
    second = manager.submit("render", lambda _cancel, _progress: "artifact", mutation=True)

    qtbot.waitUntil(lambda: len(received) == 2)
    assert set(received) == {
        ("speaker-create", first, "speaker"),
        ("render", second, "artifact"),
    }
