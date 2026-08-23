from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from src.analysis.cache import AnalysisCache
from src.audio.decoding import track_from_samples
from src.model import Project
from src.ui.track_workspace import DEFAULT_TRACK_HEIGHT, MIN_TRACK_HEIGHT
from src.ui.waveform_view import WaveformWorkspace


def make_track(name: str):
    rate = 8_000
    times = np.arange(round(rate * 0.2)) / rate
    samples = np.sin(2 * np.pi * 440 * times).astype(np.float32)
    return track_from_samples(
        samples,
        rate,
        name=name,
        project_rate=rate,
        analysis_rate=rate,
    )


def make_workspace(qtbot, track_count: int = 1):
    project = Project(playback_rate=8_000)
    project.settings.analysis_rate = 8_000
    for index in range(track_count):
        project.add_track(make_track(f"track {index + 1}"))
    workspace = WaveformWorkspace(project, AnalysisCache())
    qtbot.addWidget(workspace)
    workspace.resize(700, 400)
    workspace.show()
    workspace.rebuild()
    return workspace, project


def send_wheel(widget, delta: int, modifiers=QtCore.Qt.KeyboardModifier.NoModifier) -> None:
    target = widget.viewport()
    position = QtCore.QPointF(target.rect().center())
    global_position = QtCore.QPointF(target.mapToGlobal(position.toPoint()))
    event = QtGui.QWheelEvent(
        position,
        global_position,
        QtCore.QPoint(),
        QtCore.QPoint(0, delta),
        QtCore.Qt.MouseButton.NoButton,
        modifiers,
        QtCore.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QtWidgets.QApplication.sendEvent(target, event)


def test_track_lanes_have_a_minimum_height_and_scroll(qtbot) -> None:
    workspace, project = make_workspace(qtbot, track_count=6)

    assert all(lane.minimumHeight() >= MIN_TRACK_HEIGHT for lane in workspace.lanes.values())
    assert all(lane.preferred_height == DEFAULT_TRACK_HEIGHT for lane in workspace.lanes.values())
    assert all(lane.height() == DEFAULT_TRACK_HEIGHT for lane in workspace.lanes.values())
    qtbot.waitUntil(lambda: workspace.verticalScrollBar().maximum() > 0)

    assert set(workspace.lanes) == {track.id for track in project.tracks}
    assert workspace.wait_for_tasks()


def test_dragging_track_edge_resizes_lane_and_survives_rebuild(qtbot) -> None:
    workspace, project = make_workspace(qtbot)
    track = project.tracks[0]
    lane = workspace.lanes[track.id]
    handle = lane.resize_handle
    start = handle.rect().center()

    QtTest.QTest.mousePress(handle, QtCore.Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseMove(handle, QtCore.QPoint(start.x(), start.y() + 60), delay=10)
    QtTest.QTest.mouseRelease(
        handle,
        QtCore.Qt.MouseButton.LeftButton,
        pos=QtCore.QPoint(start.x(), start.y() + 60),
    )

    assert lane.preferred_height == DEFAULT_TRACK_HEIGHT + 60
    qtbot.waitUntil(lambda: lane.height() == DEFAULT_TRACK_HEIGHT + 60)
    lane.set_preferred_height(0)
    assert lane.preferred_height == MIN_TRACK_HEIGHT
    lane.set_preferred_height(DEFAULT_TRACK_HEIGHT + 60)
    workspace.rebuild()
    assert workspace.lanes[track.id].preferred_height == DEFAULT_TRACK_HEIGHT + 60
    assert workspace.wait_for_tasks()


def test_wheel_scrolls_tracks_and_shift_wheel_pans_timeline(qtbot) -> None:
    workspace, project = make_workspace(qtbot, track_count=6)
    qtbot.waitUntil(lambda: workspace.verticalScrollBar().maximum() > 0)
    plot = workspace.plots[project.tracks[0].id]

    send_wheel(plot, -120)
    assert workspace.verticalScrollBar().value() > 0

    vertical_position = workspace.verticalScrollBar().value()
    project.viewport.set(0.02, 0.12, project.duration)
    workspace.pan_requested.connect(
        lambda delta: project.viewport.pan(delta, project.duration)
    )
    timeline_start = project.viewport.start
    send_wheel(plot, -120, QtCore.Qt.KeyboardModifier.ShiftModifier)

    assert workspace.verticalScrollBar().value() == vertical_position
    assert project.viewport.start > timeline_start
    assert workspace.wait_for_tasks()
