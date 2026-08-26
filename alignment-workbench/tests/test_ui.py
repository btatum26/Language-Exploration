from __future__ import annotations

import numpy as np
import pytest

from alignment_workbench.analysis.tasks import AnalysisCoordinator
from alignment_workbench.state.editor import DisplayMode, EditorSession
from alignment_workbench.ui.timeline import TimelineEditor
from alignment_workbench.ui.track_widget import TrackWidget


def test_both_wave_spec_modes_keep_tiers_and_timeline_state(qtbot) -> None:
    session = EditorSession(sample_rate=8_000)
    track = session.add_audio_track(
        np.sin(np.linspace(0, 20, 8_000, dtype=np.float32)),
        name="fixture",
        identity="fixture",
    )
    session.set_selection(1_000, 2_000)
    session.set_playhead(1_500)
    session.viewport.set(500, 4_000, session.total_frames)
    coordinator = AnalysisCoordinator()
    widget = TrackWidget(session, track, coordinator)
    qtbot.addWidget(widget)
    widget.show()
    initial = (
        session.playhead_frame,
        session.selection.start,
        session.selection.end,
        session.viewport.start,
        session.viewport.end,
    )

    session.set_display_mode(track.id, DisplayMode.WAVE)
    widget.apply_display_mode()
    assert widget.waveform.isVisible()
    assert not widget.spectrogram.isVisible()
    assert widget.tier.isVisible()

    session.set_display_mode(track.id, DisplayMode.SPEC)
    widget.apply_display_mode()
    assert not widget.waveform.isVisible()
    assert widget.spectrogram.isVisible()
    assert widget.tier.isVisible()

    session.set_display_mode(track.id, DisplayMode.BOTH)
    widget.apply_display_mode()
    assert widget.waveform.isVisible() and widget.spectrogram.isVisible()
    assert initial == (
        session.playhead_frame,
        session.selection.start,
        session.selection.end,
        session.viewport.start,
        session.viewport.end,
    )
    assert coordinator.wait(30_000)


def test_segment_selection_highlights_exact_interval() -> None:
    from alignment_workbench.state.editor import Segment

    session = EditorSession(sample_rate=1_000)
    segment = Segment("phone-1", "phone", "a", 100, 240, timebase_sample_rate_hz=1_000)
    track = session.add_audio_track(
        np.zeros(1_000, dtype=np.float32),
        name="fixture",
        identity="fixture",
        phones=[segment],
    )
    session.select_segment(track.id, segment.id)
    assert (session.selection.start, session.selection.end) == (100, 240)
    assert session.playhead_frame == 100


def test_track_height_is_user_controlled_persists_and_overflows(qtbot) -> None:
    session = EditorSession(sample_rate=1_000)
    first = session.add_audio_track(
        np.zeros(10_000, dtype=np.float32), name="first", identity="first"
    )
    session.add_audio_track(np.zeros(10_000, dtype=np.float32), name="second", identity="second")
    timeline = TimelineEditor(session)
    qtbot.addWidget(timeline)
    timeline.resize(900, 360)
    timeline.show()

    first_widget = timeline.track_widgets[first.id]
    first_widget.set_preferred_height(480)
    qtbot.waitUntil(lambda: timeline.scroller.verticalScrollBar().maximum() > 0)
    assert first_widget.height() == 480
    assert timeline.track_heights[first.id] == 480

    timeline.rebuild()
    assert timeline.track_widgets[first.id].preferred_height == 480
    timeline.close()


def test_segment_tier_uses_the_exact_plot_viewbox_coordinates(qtbot) -> None:
    session = EditorSession(sample_rate=1_000)
    track = session.add_audio_track(
        np.zeros(10_000, dtype=np.float32), name="fixture", identity="fixture"
    )
    session.viewport.set(1_000, 5_000, session.total_frames)
    widget = TrackWidget(session, track, AnalysisCoordinator())
    qtbot.addWidget(widget)
    widget.resize(900, 315)
    widget.show()
    qtbot.waitUntil(lambda: widget.tier.timeline_left > 0)

    view_rect = widget.waveform.plotItem.vb.sceneBoundingRect()
    plot_left = widget.waveform.mapFromScene(view_rect.topLeft()).x()
    plot_right = widget.waveform.mapFromScene(view_rect.topRight()).x()
    assert widget.tier.timeline_left == plot_left
    assert widget.tier.timeline_left + widget.tier.timeline_width == plot_right
    assert widget.tier._frame(plot_left) == 1_000
    assert widget.tier._frame(plot_right) == 5_000
    assert widget.tier._x(3_000) == pytest.approx((plot_left + plot_right) / 2, abs=1)


def test_shared_horizontal_scroll_and_plot_pan_reach_the_right_edge(qtbot) -> None:
    session = EditorSession(sample_rate=1_000)
    session.add_audio_track(np.zeros(10_000, dtype=np.float32), name="fixture", identity="fixture")
    session.viewport.set(0, 2_000, session.total_frames)
    timeline = TimelineEditor(session)
    qtbot.addWidget(timeline)
    timeline.resize(900, 400)
    timeline.show()
    timeline.update_viewport()

    assert timeline.horizontal_scroll.maximum() == 8_000
    timeline.horizontal_scroll.setValue(8_000)
    assert (session.viewport.start, session.viewport.end) == (8_000, 10_000)

    timeline.horizontal_scroll.setValue(0)
    timeline._queue_pan(3.0)
    timeline._finish_pan()
    assert (session.viewport.start, session.viewport.end) == (3_000, 5_000)
    assert timeline.horizontal_scroll.value() == 3_000
    timeline.close()


def test_track_events_reuse_widgets_instead_of_rebuilding_timeline(qtbot) -> None:
    session = EditorSession(sample_rate=1_000)
    first = session.add_audio_track(
        np.zeros(2_000, dtype=np.float32), name="first", identity="first"
    )
    second = session.add_audio_track(
        np.zeros(2_000, dtype=np.float32), name="second", identity="second"
    )
    timeline = TimelineEditor(session)
    qtbot.addWidget(timeline)
    timeline.show()
    first_widget = timeline.track_widgets[first.id]
    second_widget = timeline.track_widgets[second.id]
    first_generation = first_widget.generation

    for value in range(110, 161, 10):
        first_widget.gain_slider.setValue(value)

    assert timeline.track_widgets[first.id] is first_widget
    assert timeline.track_widgets[second.id] is second_widget
    assert first_widget.generation == first_generation

    session.set_track_visible(first.id, False)
    session.set_reference_track(second.id)
    session.reorder_track(second.id, -1)

    assert timeline.track_widgets[first.id] is first_widget
    assert timeline.track_widgets[second.id] is second_widget
    assert not first_widget.analysis_container.isVisible()
    assert second_widget.reference_button.isChecked()
    assert timeline.track_layout.indexOf(second_widget) == 0
    assert timeline.track_layout.indexOf(first_widget) == 1

    third = session.add_audio_track(
        np.zeros(2_000, dtype=np.float32), name="third", identity="third"
    )
    assert timeline.track_widgets[first.id] is first_widget
    assert timeline.track_widgets[second.id] is second_widget
    assert third.id in timeline.track_widgets

    session.remove_track(second.id)
    assert timeline.track_widgets[first.id] is first_widget
    assert second.id not in timeline.track_widgets
    timeline.close()
