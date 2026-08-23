from __future__ import annotations

import threading
import time

import numpy as np
from PySide6 import QtCore, QtTest

from src.audio.decoding import track_from_samples
from src.audio.engine import AudioEngine, FakeOutputBackend
from src.model import Project
from src.ui.main_window import MainWindow


def make_track(name: str, duration: float = 0.2):
    rate = 8_000
    time = np.arange(round(rate * duration)) / rate
    samples = np.sin(2 * np.pi * 440 * time).astype(np.float32)
    return track_from_samples(
        samples,
        rate,
        name=name,
        project_rate=8_000,
        analysis_rate=8_000,
    )


def make_window(qtbot):
    project = Project(playback_rate=8_000)
    project.settings.analysis_rate = 8_000
    backend = FakeOutputBackend()
    window = MainWindow(project, AudioEngine(project, backend))
    qtbot.addWidget(window)
    window.show()
    return window


def test_adding_track_does_not_replace_existing_tracks(qtbot) -> None:
    window = make_window(qtbot)
    window.add_track(make_track("first"))
    first_id = window.project.tracks[0].id
    window.add_track(make_track("second", 0.3))
    assert len(window.project.tracks) == 2
    assert window.project.tracks[0].id == first_id


def test_linked_waveform_plots_keep_identical_ranges(qtbot) -> None:
    window = make_window(qtbot)
    window.add_track(make_track("first"))
    window.add_track(make_track("second", 0.3))
    window.project.viewport.set(0.05, 0.2, window.project.duration)
    window._refresh_timeline()
    ranges = window.waveform.x_ranges()
    assert len(ranges) == 2
    assert all(np.allclose(item, ranges[0]) for item in ranges[1:])


def test_pan_drag_keeps_moving_when_viewport_changes_between_events(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("first", 1.0)
    window.add_track(track)
    window.project.viewport.set(0.2, 0.8, window.project.duration)
    window._refresh_timeline()
    plot = window.waveform.plots[track.id]
    center = plot.viewport().rect().center()

    qtbot.mousePress(plot.viewport(), QtCore.Qt.MouseButton.MiddleButton, pos=center)
    qtbot.mouseMove(plot.viewport(), pos=QtCore.QPoint(center.x() + 30, center.y()))
    after_first_move = window.project.viewport.start
    qtbot.mouseMove(plot.viewport(), pos=QtCore.QPoint(center.x() + 60, center.y()))
    after_second_move = window.project.viewport.start
    qtbot.mouseRelease(
        plot.viewport(),
        QtCore.Qt.MouseButton.MiddleButton,
        pos=QtCore.QPoint(center.x() + 60, center.y()),
    )

    assert after_first_move < 0.2
    assert after_second_move < after_first_move
    assert 0.5 < (0.2 - after_second_move) / (0.2 - after_first_move) < 3.0


def test_playhead_tick_does_not_reapply_plot_range(qtbot, monkeypatch) -> None:
    window = make_window(qtbot)
    track = make_track("first", 1.0)
    window.add_track(track)
    plot = window.waveform.plots[track.id]
    range_updates: list[tuple[object, ...]] = []
    monkeypatch.setattr(plot, "setXRange", lambda *args, **kwargs: range_updates.append(args))

    window._refresh_playhead()

    assert range_updates == []


def test_plot_click_updates_shared_playhead(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("first", 1.0)
    window.add_track(track)
    plot = window.waveform.plots[track.id]
    qtbot.mouseClick(
        plot.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=plot.viewport().rect().center()
    )
    assert 0.2 < window.engine.current_time < 0.8


def test_left_click_inside_selection_still_moves_playhead(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("first", 1.0)
    window.add_track(track)
    window.set_track_selection(track.id, 0.2, 0.8)
    window.seek(0.0)
    plot = window.waveform.plots[track.id]
    qtbot.mouseClick(
        plot.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=plot.viewport().rect().center()
    )
    assert 0.2 < window.engine.current_time < 0.8


def test_spectrogram_click_updates_shared_playhead(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("first", 1.0)
    window.add_track(track)
    plot = window.spectrogram.plots[track.id]
    qtbot.mouseClick(
        plot.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        pos=plot.viewport().rect().center(),
    )
    assert 0.2 < window.engine.current_time < 0.8


def test_selection_drag_updates_model_and_fourier_interval(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("first", 1.0)
    window.add_track(track)
    plot = window.waveform.plots[track.id]
    left = plot.viewport().rect().center()
    right = QtCore.QPoint(left.x() + 80, left.y())
    QtTest.QTest.mousePress(
        plot.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.ControlModifier,
        left,
    )
    qtbot.mouseMove(plot.viewport(), pos=right)
    qtbot.mouseRelease(plot.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=right)
    assert window.project.selection.active
    window.tabs.setCurrentIndex(2)
    assert np.allclose(
        window.fourier.last_interval, (window.project.selection.start, window.project.selection.end)
    )


def test_track_selections_are_independent_and_render_only_on_their_lanes(qtbot) -> None:
    window = make_window(qtbot)
    first = make_track("first", 1.0)
    second = make_track("second", 1.0)
    window.add_track(first)
    window.add_track(second)
    window.set_track_selection(first.id, 0.1, 0.3)
    window.set_track_selection(second.id, 0.5, 0.8)
    assert np.allclose(window.waveform.regions[first.id].getRegion(), (0.1, 0.3))
    assert np.allclose(window.waveform.regions[second.id].getRegion(), (0.5, 0.8))
    assert window.project.active_track_id == second.id
    assert np.allclose((window.project.selection.start, window.project.selection.end), (0.5, 0.8))


def test_copy_paste_and_delete_edit_the_active_track(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("edit me", 1.0)
    original = track.playback_view.copy()
    window.add_track(track)
    window.set_selection(0.2, 0.4)
    assert window.copy_selection()
    window.seek(0.8)
    window.paste_selection()
    assert np.isclose(track.duration, 1.2)
    assert np.allclose((window.project.selection.start, window.project.selection.end), (0.8, 1.0))
    window.delete_selection()
    assert np.isclose(track.duration, 1.0)
    np.testing.assert_array_equal(track.playback_view, original)


def test_tabs_preserve_playhead_selection_and_viewport(qtbot) -> None:
    window = make_window(qtbot)
    window.add_track(make_track("first", 1.0))
    window.seek(0.4)
    window.set_selection(0.2, 0.6)
    window.project.viewport.set(0.1, 0.8, window.project.duration)
    before = (
        window.project.transport.frame_position,
        window.project.selection.start,
        window.project.selection.end,
        window.project.viewport.start,
        window.project.viewport.end,
    )
    window.tabs.setCurrentIndex(1)
    window.tabs.setCurrentIndex(2)
    after = (
        window.project.transport.frame_position,
        window.project.selection.start,
        window.project.selection.end,
        window.project.viewport.start,
        window.project.viewport.end,
    )
    assert after == before


def test_record_action_is_wired_and_feedback_is_visible(qtbot) -> None:
    window = make_window(qtbot)
    window.toolbar.record_requested.disconnect(window.toggle_recording)
    with qtbot.waitSignal(window.toolbar.record_requested):
        window.toolbar.record_action.trigger()
    window.toolbar.set_recording_feedback(True, 2.4, 0.5)
    assert window.toolbar.recording_label.isVisible()
    assert "REC 00:02.4" in window.toolbar.recording_label.text()
    assert window.toolbar.input_level.value() == 50
    window.recording_indicator.setVisible(True)
    window.recording_indicator.update_recording(2.4, 0.5, "Test microphone")
    assert window.recording_indicator.isVisible()
    assert "RECORDING 00:02.4" in window.recording_indicator.status.text()
    assert window.recording_indicator.device.text() == "Test microphone"
    assert window.recording_indicator.level.value() == 50


def test_fourier_analysis_does_not_block_qt_thread(qtbot, monkeypatch) -> None:
    import src.ui.fourier_view as fourier_module

    window = make_window(qtbot)
    window.add_track(make_track("first", 1.0))
    worker_started = threading.Event()
    release_worker = threading.Event()
    worker_threads: list[int] = []
    original = fourier_module.calculate_spectrum

    def slow_spectrum(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        worker_started.set()
        assert release_worker.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(fourier_module, "calculate_spectrum", slow_spectrum)
    started_at = time.perf_counter()
    window.tabs.setCurrentIndex(2)
    assert time.perf_counter() - started_at < 0.2
    qtbot.waitUntil(worker_started.is_set, timeout=2_000)

    qt_event_processed: list[bool] = []
    QtCore.QTimer.singleShot(0, lambda: qt_event_processed.append(True))
    qtbot.waitUntil(lambda: bool(qt_event_processed), timeout=1_000)
    assert worker_threads[0] != threading.get_ident()
    assert window.fourier.region_label.text().startswith("Analyzing")

    with qtbot.waitSignal(window.fourier.analysis_finished, timeout=5_000):
        release_worker.set()


def test_active_track_can_trim_to_playhead_and_reset(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("trim me", 1.0)
    source = track.source_samples.copy()
    window.add_track(track)
    window.seek(0.25)
    window.trim_start_to_playhead()
    assert track.duration == 0.75
    assert track.trim_bounds_seconds == (0.25, 1.0)
    np.testing.assert_array_equal(track.source_samples, source)
    window.reset_active_trim()
    assert track.duration == 1.0
    assert not track.is_trimmed


def test_active_track_can_keep_only_shared_selection(qtbot) -> None:
    window = make_window(qtbot)
    track = make_track("trim me", 1.0)
    window.add_track(track)
    window.set_selection(0.2, 0.6)
    window.trim_to_selection()
    assert np.isclose(track.duration, 0.4)
    assert track.trim_bounds_seconds == (0.2, 0.6)
    assert not window.project.selection.active
