from __future__ import annotations

import numpy as np
import pytest

from alignment_workbench.state.editor import DisplayMode, EditorSession, Segment


def session_with_track(frames: int = 12) -> tuple[EditorSession, object]:
    session = EditorSession(sample_rate=12)
    track = session.add_audio_track(
        np.arange(frames, dtype=np.float32) / max(1, frames),
        name="take",
        identity="fixture",
    )
    return session, track


def test_shared_playhead_selection_and_stable_frame_arithmetic() -> None:
    session, _track = session_with_track()
    session.set_playhead(5)
    session.set_selection(9, 3)

    assert session.playhead_frame == 5
    assert (session.selection.start, session.selection.end) == (3, 9)
    assert session.selection.duration_frames == 6
    assert session.duration_seconds == 1.0


def test_split_move_and_undo_redo() -> None:
    session, track = session_with_track()
    session.split_at(4)
    assert [(clip.timeline_start_frame, clip.frame_count) for clip in track.clips] == [
        (0, 4),
        (4, 8),
    ]

    moved_id = track.clips[1].id
    session.move_clip(moved_id, 3)
    assert track.clips[1].timeline_start_frame == 7
    session.commands.undo()
    assert track.clips[1].timeline_start_frame == 4
    session.commands.undo()
    assert len(track.clips) == 1
    session.commands.redo()
    session.commands.redo()
    assert track.clips[1].timeline_start_frame == 7


def test_copy_cut_paste_delete_are_non_destructive_clip_edits() -> None:
    session, track = session_with_track()
    source = next(iter(session.sources.values()))
    original = source.samples.copy()
    session.set_selection(3, 7)
    copied = session.copy_selection()
    assert copied[0].frame_count == 4

    session.cut_selection()
    np.testing.assert_allclose(
        session.render_track(track.id), np.concatenate((original[:3], original[7:]))
    )
    np.testing.assert_array_equal(source.samples, original)

    session.paste(2)
    assert session.total_frames == len(original)
    assert session.selection.duration_frames == 4
    session.commands.undo()
    assert session.total_frames == len(original) - 4
    session.commands.redo()
    assert session.total_frames == len(original)

    session.set_selection(2, 6)
    session.delete_selection()
    assert session.total_frames == len(original) - 4


def test_delete_entire_track_audio_is_rejected() -> None:
    session, _track = session_with_track()
    session.set_selection(0, session.total_frames)
    with pytest.raises(ValueError, match="Delete the track"):
        session.delete_selection()


def test_add_audio_to_existing_track_is_undoable() -> None:
    session, track = session_with_track()
    session.set_playhead(4)
    clip = session.add_audio_clip(
        track.id,
        np.ones(3, dtype=np.float32),
        identity="inserted",
    )

    assert clip.timeline_start_frame == 4
    assert clip in track.clips
    session.commands.undo()
    assert clip not in track.clips
    session.commands.redo()
    assert clip in track.clips


def test_track_reordering_and_per_track_display_mode_preserve_timeline() -> None:
    session, first = session_with_track()
    second = session.add_audio_track(
        np.ones(8, dtype=np.float32), name="second", identity="fixture-2"
    )
    session.set_playhead(5)
    session.set_selection(2, 6)
    session.viewport.set(1, 8, session.total_frames)
    before = (
        session.playhead_frame,
        session.selection.start,
        session.selection.end,
        session.viewport.start,
        session.viewport.end,
    )

    session.set_display_mode(first.id, DisplayMode.WAVE)
    session.set_display_mode(second.id, DisplayMode.SPEC)
    session.reorder_track(second.id, -1)

    assert [track.id for track in session.tracks] == [second.id, first.id]
    assert first.display_mode is DisplayMode.WAVE
    assert second.display_mode is DisplayMode.SPEC
    assert before == (
        session.playhead_frame,
        session.selection.start,
        session.selection.end,
        session.viewport.start,
        session.viewport.end,
    )


def test_track_mutations_emit_targeted_events() -> None:
    session, first = session_with_track()
    events = []
    session.subscribe(events.append)

    session.set_track_mix(first.id, gain=1.5)
    session.set_track_mix(first.id, muted=True)
    session.set_track_visible(first.id, False)
    session.set_track_name(first.id, "renamed")
    session.set_display_mode(first.id, DisplayMode.WAVE)
    second = session.add_audio_track(
        np.ones(8, dtype=np.float32), name="second", identity="fixture-2"
    )
    session.set_reference_track(second.id)
    session.reorder_track(second.id, -1)
    session.active_track_id = first.id
    session.split_at(4)
    session.remove_track(second.id)

    assert [(event.reason, event.track_id) for event in events] == [
        ("track-mix", first.id),
        ("track-mix", first.id),
        ("track-layout", first.id),
        ("track-layout", first.id),
        ("track-layout", first.id),
        ("track-added", second.id),
        ("track-layout", None),
        ("track-order", second.id),
        ("history", None),
        ("track-content", first.id),
        ("history", None),
        ("track-removed", second.id),
    ]


def test_revert_restores_saved_effective_revision_not_original_model() -> None:
    session = EditorSession(sample_rate=1_000)
    saved = Segment(
        id="phone",
        kind="phone",
        label="ɛ",
        start_frame=120,
        end_frame=240,
        model_label="e",
        model_start_frame=100,
        model_end_frame=250,
        saved_label="ɛ",
        saved_start_frame=120,
        saved_end_frame=240,
    )
    track = session.add_audio_track(
        np.zeros(1_000, dtype=np.float32),
        name="fixture",
        identity="fixture",
        phones=[saved],
    )
    session.update_segment(
        track.id,
        Segment(
            id="phone",
            kind="phone",
            label="a",
            start_frame=130,
            end_frame=230,
            model_label="e",
            model_start_frame=100,
            model_end_frame=250,
            saved_label="ɛ",
            saved_start_frame=120,
            saved_end_frame=240,
        ),
    )
    session.revert_segment(track.id, "phone")
    reverted = session.segment(track.id, "phone")
    assert (reverted.label, reverted.start_frame, reverted.end_frame) == ("ɛ", 120, 240)
