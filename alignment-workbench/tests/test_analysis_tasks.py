from __future__ import annotations

import threading

import numpy as np

from alignment_workbench.analysis import tasks as task_module
from alignment_workbench.analysis.tasks import AnalysisCoordinator


def test_old_slow_analysis_cannot_replace_new_same_length_result(qtbot, monkeypatch) -> None:
    release_old = threading.Event()
    received: list[tuple[int, object]] = []

    def waveform(samples, _rate):
        if float(samples[0]) == 0.0:
            release_old.wait(2)
        return f"wave-{float(samples[0])}"

    monkeypatch.setattr(task_module, "create_envelope", waveform)
    monkeypatch.setattr(
        task_module,
        "calculate_spectrogram",
        lambda samples, _rate, **_settings: f"spec-{float(samples[0])}",
    )
    coordinator = AnalysisCoordinator()
    coordinator.completed.connect(
        lambda _track, generation, result: received.append((generation, result.waveform))
    )
    track_id = __import__("uuid").uuid4()
    coordinator.analyze(track_id, 1, np.zeros(20, dtype=np.float32), 1_000)
    coordinator.analyze(track_id, 2, np.ones(20, dtype=np.float32), 1_000)

    qtbot.waitUntil(lambda: received == [(2, "wave-1.0")])
    release_old.set()
    qtbot.waitUntil(
        lambda: (
            coordinator.cache.get(coordinator.cache.key(track_id, "workbench-waveform", 1, 20))
            is None
        )
    )
    assert coordinator.wait(5_000)
    qtbot.waitUntil(lambda: not coordinator._tasks)
    assert received == [(2, "wave-1.0")]
    assert (
        coordinator.cache.get(coordinator.cache.key(track_id, "workbench-waveform", 1, 20)) is None
    )
