from __future__ import annotations

from unittest.mock import Mock

import numpy as np
import pyqtgraph as pg
import pytest
from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from src.model.transport import TransportState

from alignment_workbench.analysis.tasks import AnalysisCoordinator
from alignment_workbench.state.editor import EditorSession, SessionEventType
from alignment_workbench.ui.main_window import MainWindow
from alignment_workbench.ui.timeline import TimelineEditor


def send_wheel(
    plot,
    delta: int,
    modifiers: QtCore.Qt.KeyboardModifier = QtCore.Qt.KeyboardModifier.NoModifier,
) -> None:
    target = plot.viewport()
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


@pytest.fixture
def timeline_scene(qtbot, monkeypatch):
    monkeypatch.setattr(AnalysisCoordinator, "analyze", lambda *_args, **_kwargs: None)
    session = EditorSession(sample_rate=1_000)
    first = session.add_audio_track(
        np.zeros(10_000, dtype=np.float32), name="first", identity="first"
    )
    second = session.add_audio_track(
        np.zeros(10_000, dtype=np.float32), name="second", identity="second"
    )
    session.viewport.set(2_000, 6_000, session.total_frames)
    timeline = TimelineEditor(session)
    qtbot.addWidget(timeline)
    timeline.resize(900, 700)
    timeline.show()
    yield session, timeline, first, second
    timeline.close()


def test_many_pan_inputs_are_coalesced_without_losing_movement(qtbot, timeline_scene) -> None:
    session, timeline, _first, _second = timeline_scene
    viewport_events = []
    session.subscribe(
        lambda event: (
            viewport_events.append(event) if event.reason is SessionEventType.VIEWPORT else None
        )
    )
    start = session.viewport.start

    for _ in range(100):
        timeline._queue_pan(0.001)

    assert viewport_events == []
    assert timeline._pan_timer.interval() == 16
    qtbot.waitUntil(lambda: len(viewport_events) == 1, timeout=1_000)
    assert session.viewport.start == start + 100


def test_finishing_pan_applies_the_final_pending_delta(timeline_scene) -> None:
    session, timeline, _first, _second = timeline_scene
    start = session.viewport.start

    timeline._queue_pan(0.125)
    timeline._finish_pan()

    assert session.viewport.start == start + 125
    assert not timeline._pan_timer.isActive()
    assert timeline._pending_pan_frames == 0


def test_state_events_call_only_the_matching_track_update(timeline_scene, monkeypatch) -> None:
    session, timeline, first, _second = timeline_scene
    widget = timeline.track_widgets[first.id]
    viewport = Mock()
    playhead = Mock()
    selection = Mock()
    segments = Mock()
    monkeypatch.setattr(widget, "update_viewport", viewport)
    monkeypatch.setattr(widget, "update_playhead", playhead)
    monkeypatch.setattr(widget, "update_selection", selection)
    monkeypatch.setattr(widget, "update_segments", segments)

    session.pan_viewport(100)
    assert viewport.call_count == 1
    assert playhead.call_count == selection.call_count == segments.call_count == 0

    session.set_playhead(250)
    assert playhead.call_count == 1
    assert viewport.call_count == 1
    assert selection.call_count == segments.call_count == 0

    session.set_selection(100, 300)
    assert selection.call_count == 1
    assert viewport.call_count == 1
    assert playhead.call_count == 1
    assert segments.call_count == 0


def test_focused_updates_do_not_touch_unrelated_plot_items(timeline_scene, monkeypatch) -> None:
    session, timeline, first, _second = timeline_scene
    widget = timeline.track_widgets[first.id]
    waveform_range = Mock()
    spectrogram_range = Mock()
    wave_playhead = Mock()
    spec_playhead = Mock()
    wave_region = Mock()
    spec_region = Mock()
    tier_update = Mock()
    monkeypatch.setattr(widget.waveform, "setXRange", waveform_range)
    monkeypatch.setattr(widget.spectrogram, "setXRange", spectrogram_range)
    monkeypatch.setattr(widget.tier, "update", tier_update)
    widget._wave_playhead = wave_playhead
    widget._spec_playhead = spec_playhead
    widget._wave_region = wave_region
    widget._spec_region = spec_region
    widget._reset_timeline_cache()

    session.viewport.pan(100, session.total_frames)
    widget.update_viewport()
    assert waveform_range.call_count == spectrogram_range.call_count == 1
    assert tier_update.call_count == 1
    wave_playhead.setValue.assert_not_called()
    spec_playhead.setValue.assert_not_called()
    wave_region.setRegion.assert_not_called()
    spec_region.setRegion.assert_not_called()

    session.playhead_frame = 400
    widget.update_playhead()
    wave_playhead.setValue.assert_called_once()
    spec_playhead.setValue.assert_called_once()
    assert waveform_range.call_count == spectrogram_range.call_count == 1
    assert tier_update.call_count == 1
    wave_region.setRegion.assert_not_called()
    spec_region.setRegion.assert_not_called()

    session.selection.set(200, 500, session.total_frames)
    widget.update_selection()
    wave_region.setRegion.assert_called_once()
    spec_region.setRegion.assert_called_once()
    assert waveform_range.call_count == spectrogram_range.call_count == 1
    assert tier_update.call_count == 1


def test_panning_does_not_rebuild_widgets_or_schedule_analysis(timeline_scene, monkeypatch) -> None:
    session, timeline, _first, _second = timeline_scene
    widgets = dict(timeline.track_widgets)
    generations = {track_id: widget.generation for track_id, widget in widgets.items()}
    rebuild = Mock()
    analyze = Mock()
    monkeypatch.setattr(timeline, "rebuild", rebuild)
    monkeypatch.setattr(timeline.analysis, "analyze", analyze)

    timeline._queue_pan(0.2)
    timeline._finish_pan()

    assert session.viewport.start == 2_200
    assert timeline.track_widgets == widgets
    assert {track_id: widget.generation for track_id, widget in widgets.items()} == generations
    rebuild.assert_not_called()
    analyze.assert_not_called()


def test_scrollbar_metrics_are_not_reconfigured_during_pan(timeline_scene, monkeypatch) -> None:
    session, timeline, _first, _second = timeline_scene
    set_range = Mock(wraps=timeline.horizontal_scroll.setRange)
    set_page = Mock(wraps=timeline.horizontal_scroll.setPageStep)
    set_step = Mock(wraps=timeline.horizontal_scroll.setSingleStep)
    set_value = Mock(wraps=timeline.horizontal_scroll.setValue)
    monkeypatch.setattr(timeline.horizontal_scroll, "setRange", set_range)
    monkeypatch.setattr(timeline.horizontal_scroll, "setPageStep", set_page)
    monkeypatch.setattr(timeline.horizontal_scroll, "setSingleStep", set_step)
    monkeypatch.setattr(timeline.horizontal_scroll, "setValue", set_value)

    session.pan_viewport(100)
    set_range.assert_not_called()
    set_page.assert_not_called()
    set_step.assert_not_called()
    set_value.assert_called_once_with(2_100)

    session.zoom_viewport(0.5, 4_000)
    assert set_range.call_count == set_page.call_count == set_step.call_count == 1


def test_all_tracks_stay_synchronized_after_pan_and_zoom(timeline_scene) -> None:
    session, timeline, _first, _second = timeline_scene

    timeline._zoom(0.5, 4.0)
    timeline._queue_pan(0.25)
    timeline._finish_pan()

    expected = np.array(
        [
            session.viewport.start / session.sample_rate,
            session.viewport.end / session.sample_rate,
        ]
    )
    for widget in timeline.track_widgets.values():
        assert widget._last_viewport == (session.viewport.start, session.viewport.end)
        np.testing.assert_allclose(widget.waveform.viewRange()[0], expected)
        np.testing.assert_allclose(widget.spectrogram.viewRange()[0], expected)


def test_coalesced_pan_clamps_cleanly_at_both_timeline_boundaries(timeline_scene) -> None:
    session, timeline, _first, _second = timeline_scene

    session.set_viewport(0, 4_000)
    timeline._queue_pan(-2.0)
    timeline._finish_pan()
    assert (session.viewport.start, session.viewport.end) == (0, 4_000)
    assert timeline._pending_pan_frames == 0

    session.set_viewport(6_000, 10_000)
    timeline._queue_pan(2.0)
    timeline._finish_pan()
    assert (session.viewport.start, session.viewport.end) == (6_000, 10_000)
    assert timeline._pending_pan_frames == 0


def test_middle_drag_and_shift_wheel_use_shared_pan_controller(qtbot, timeline_scene) -> None:
    session, timeline, first, _second = timeline_scene
    plot = timeline.track_widgets[first.id].waveform
    center = plot.viewport().rect().center()
    start = session.viewport.start

    QtTest.QTest.mousePress(plot.viewport(), QtCore.Qt.MouseButton.MiddleButton, pos=center)
    QtTest.QTest.mouseMove(plot.viewport(), QtCore.QPoint(center.x() + 50, center.y()))
    QtTest.QTest.mouseRelease(
        plot.viewport(),
        QtCore.Qt.MouseButton.MiddleButton,
        pos=QtCore.QPoint(center.x() + 50, center.y()),
    )
    assert session.viewport.start < start

    session.set_viewport(2_000, 6_000)
    start = session.viewport.start
    send_wheel(plot, -120, QtCore.Qt.KeyboardModifier.ShiftModifier)
    qtbot.waitUntil(lambda: session.viewport.start > start, timeout=1_000)


def test_playback_tick_uses_only_playhead_path(qtbot, monkeypatch) -> None:
    monkeypatch.setattr(AnalysisCoordinator, "analyze", lambda *_args, **_kwargs: None)
    window = MainWindow(services=None)
    qtbot.addWidget(window)
    track = window.session.add_audio_track(
        np.zeros(2_000, dtype=np.float32), name="fixture", identity="fixture"
    )
    widget = window.timeline.track_widgets[track.id]
    viewport = Mock()
    playhead = Mock()
    selection = Mock()
    segments = Mock()
    monkeypatch.setattr(widget, "update_viewport", viewport)
    monkeypatch.setattr(widget, "update_playhead", playhead)
    monkeypatch.setattr(widget, "update_selection", selection)
    monkeypatch.setattr(widget, "update_segments", segments)
    focus_changed = Mock()
    monkeypatch.setattr(window, "_focus_changed", focus_changed)
    window.audio.project.transport.state = TransportState.PLAYING
    window.audio.project.transport.frame_position = 250

    window._tick()

    assert window.session.playhead_frame == 250
    playhead.assert_called_once()
    viewport.assert_not_called()
    selection.assert_not_called()
    segments.assert_not_called()
    focus_changed.assert_not_called()

    window.session.commands.clear()
    focus_changed.assert_called_once()
    window.close()


def test_waveform_and_spectrogram_enable_safe_downsampling(qtbot) -> None:
    session = EditorSession(sample_rate=8_000)
    track = session.add_audio_track(
        np.sin(np.linspace(0, 100, 8_000, dtype=np.float32)),
        name="fixture",
        identity="fixture",
    )
    timeline = TimelineEditor(session)
    qtbot.addWidget(timeline)
    timeline.show()
    widget = timeline.track_widgets[track.id]
    qtbot.waitUntil(lambda: hasattr(widget, "_spectrogram_image"), timeout=30_000)

    assert widget._spectrogram_image.autoDownsample
    for curve in widget._waveform_curves:
        assert curve.opts["clipToView"]
        assert curve.opts["autoDownsample"]
        assert curve.opts["downsampleMethod"] == "peak"
    assert sum(isinstance(item, pg.FillBetweenItem) for item in widget.waveform.plotItem.items) == 1
    timeline.close()
