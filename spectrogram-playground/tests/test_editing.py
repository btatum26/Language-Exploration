from __future__ import annotations

import numpy as np

from src.audio.decoding import track_from_samples
from src.audio.editing import copy_region, delete_region, move_region, paste_clip


def make_track():
    samples = np.arange(10, dtype=np.float32)
    return track_from_samples(
        samples,
        10,
        name="editable",
        project_rate=10,
        analysis_rate=10,
    )


def test_copy_paste_and_delete_keep_playback_and_analysis_in_sync() -> None:
    track = make_track()
    clip = copy_region(track, 0.2, 0.4)
    inserted = paste_clip(track, 0.8, clip)
    assert inserted == (0.8, 1.0)
    expected = np.array([0, 1, 2, 3, 4, 5, 6, 7, 2, 3, 8, 9], dtype=np.float32)
    np.testing.assert_array_equal(track.playback_view, expected)
    np.testing.assert_array_equal(track.analysis_view, expected)
    delete_region(track, *inserted)
    np.testing.assert_array_equal(track.playback_view, np.arange(10, dtype=np.float32))
    np.testing.assert_array_equal(track.analysis_view, np.arange(10, dtype=np.float32))


def test_move_region_reorders_audio_and_returns_its_new_selection() -> None:
    track = make_track()
    moved = move_region(track, 0.2, 0.4, 0.8)
    assert moved == (0.6, 0.8)
    expected = np.array([0, 1, 4, 5, 6, 7, 2, 3, 8, 9], dtype=np.float32)
    np.testing.assert_array_equal(track.playback_view, expected)
    np.testing.assert_array_equal(track.analysis_view, expected)
