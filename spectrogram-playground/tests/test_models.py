from __future__ import annotations

import numpy as np

from src.audio.decoding import track_from_samples
from src.model import Project, VisualizationTab


def make_track(name: str, seconds: float = 1.0):
    return track_from_samples(
        np.zeros(round(48_000 * seconds), dtype=np.float32),
        48_000,
        name=name,
        project_rate=48_000,
        analysis_rate=16_000,
    )


def test_ids_survive_reorder_and_deletion() -> None:
    project = Project()
    tracks = [make_track(str(i)) for i in range(3)]
    for track in tracks:
        project.add_track(track)
    project.move_track(tracks[2].id, -2)
    assert [track.id for track in project.tracks] == [tracks[2].id, tracks[0].id, tracks[1].id]
    project.remove_track(tracks[0].id)
    assert [track.id for track in project.tracks] == [tracks[2].id, tracks[1].id]


def test_duration_is_longest_track_and_active_removal_is_valid() -> None:
    project = Project()
    short, long = make_track("short", 0.5), make_track("long", 2.0)
    project.add_track(short)
    project.add_track(long)
    assert project.duration == 2.0
    project.remove_track(long.id)
    assert project.active_track_id == short.id


def test_selection_and_viewport_are_bounded() -> None:
    project = Project()
    project.add_track(make_track("track", 2))
    project.selection.set(1.5, 0.5, project.duration)
    assert (project.selection.start, project.selection.end) == (0.5, 1.5)
    project.viewport.set(-20, 20, project.duration)
    assert (project.viewport.start, project.viewport.end) == (0, 2)
    project.viewport.pan(50, project.duration)
    assert project.viewport.end <= project.duration


def test_each_track_keeps_an_independent_selection() -> None:
    project = Project()
    first, second = make_track("first", 1), make_track("second", 2)
    project.add_track(first)
    project.add_track(second)
    project.selection_for(first.id).set(0.1, 0.3, first.duration)
    project.selection_for(second.id).set(0.8, 1.4, second.duration)
    assert (project.selection_for(first.id).start, project.selection_for(first.id).end) == (
        0.1,
        0.3,
    )
    assert (project.selection.start, project.selection.end) == (0.8, 1.4)


def test_tab_switch_preserves_shared_timeline_state() -> None:
    project = Project()
    project.add_track(make_track("track", 2))
    project.transport.frame_position = 24_000
    project.selection.set(0.2, 0.8, project.duration)
    project.viewport.set(0.1, 1.1, project.duration)
    before = (
        project.transport.frame_position,
        project.selection.start,
        project.selection.end,
        project.viewport.start,
        project.viewport.end,
    )
    project.active_tab = VisualizationTab.SPECTROGRAM
    after = (
        project.transport.frame_position,
        project.selection.start,
        project.selection.end,
        project.viewport.start,
        project.viewport.end,
    )
    assert after == before


def test_mute_and_solo_choose_correct_tracks() -> None:
    project = Project()
    first, second, third = (make_track(name) for name in ("a", "b", "c"))
    for track in (first, second, third):
        project.add_track(track)
    first.muted = True
    assert project.audible_tracks() == [second, third]
    third.solo = True
    assert project.audible_tracks() == [third]
    third.muted = True
    assert project.audible_tracks() == [second]


def test_trim_is_non_destructive_and_composes_relative_to_current_view() -> None:
    samples = np.arange(10, dtype=np.float32)
    track = track_from_samples(
        samples,
        10,
        name="trim",
        project_rate=10,
        analysis_rate=10,
    )
    original = track.source_samples.copy()
    track.trim(0.2, 0.8)
    np.testing.assert_array_equal(track.playback_view, samples[2:8])
    assert track.duration == 0.6
    track.trim(0.1, 0.4)
    np.testing.assert_array_equal(track.playback_view, samples[3:6])
    np.testing.assert_array_equal(track.source_samples, original)
    track.reset_trim()
    np.testing.assert_array_equal(track.playback_view, samples)
