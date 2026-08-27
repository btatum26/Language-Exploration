from __future__ import annotations

import numpy as np
import pytest
from spectrogram_playground.audio.engine import FakeOutputBackend

from alignment_workbench.audio.engine import SessionAudioEngine
from alignment_workbench.state.editor import EditorSession


def test_shared_clock_mixes_multiple_tracks_and_plays_from_arbitrary_frame() -> None:
    session = EditorSession(sample_rate=8)
    session.add_audio_track(np.full(8, 0.2, dtype=np.float32), name="a", identity="a")
    session.add_audio_track(np.full(8, 0.3, dtype=np.float32), name="b", identity="b")
    backend = FakeOutputBackend()
    engine = SessionAudioEngine(session, backend)
    engine.seek(3)
    engine.engine.play()

    output = backend.pump(3)

    np.testing.assert_allclose(output, 0.5)
    assert engine.engine.frame_position == 6
    engine.close()


def test_loop_selection_uses_one_authoritative_frame_clock() -> None:
    session = EditorSession(sample_rate=8)
    session.add_audio_track(np.arange(8, dtype=np.float32) / 8, name="a", identity="a")
    backend = FakeOutputBackend()
    engine = SessionAudioEngine(session, backend)
    session.set_selection(2, 5)
    engine.set_loop(True)
    engine.seek(4)
    engine.engine.play()

    output = backend.pump(4)

    np.testing.assert_allclose(output, np.array([0.5, 0.25, 0.375, 0.5], dtype=np.float32))
    assert engine.engine.frame_position == 2
    engine.close()


def test_track_events_update_audio_project_without_full_renders(monkeypatch, qtbot) -> None:
    session = EditorSession(sample_rate=8)
    first = session.add_audio_track(np.full(8, 0.2, dtype=np.float32), name="a", identity="a")
    second = session.add_audio_track(np.full(8, 0.3, dtype=np.float32), name="b", identity="b")
    backend = FakeOutputBackend()
    engine = SessionAudioEngine(session, backend)
    original_tracks = {track.id: track for track in engine.project.tracks}
    original_compose = EditorSession.compose_render_plan
    rendered = []

    def record_render(plan):
        rendered.append(plan.track_id)
        return original_compose(plan)

    monkeypatch.setattr(EditorSession, "compose_render_plan", staticmethod(record_render))

    engine.engine.play()
    session.set_track_mix(first.id, gain=1.5, muted=True, solo=True)
    session.set_track_name(first.id, "renamed")
    session.set_track_visible(first.id, False)
    session.reorder_track(second.id, -1)

    assert rendered == []
    assert backend.running
    assert [track.id for track in engine.project.tracks] == [second.id, first.id]
    assert engine.project.track_by_id(first.id) is original_tracks[first.id]
    assert engine.project.track_by_id(first.id).gain == 1.5
    assert engine.project.track_by_id(first.id).muted
    assert engine.project.track_by_id(first.id).solo
    assert engine.project.track_by_id(first.id).name == "renamed"
    assert not engine.project.track_by_id(first.id).visible

    session.set_active_track(first.id)
    session.split_at(4)
    qtbot.waitUntil(lambda: rendered == [first.id])
    qtbot.waitUntil(lambda: engine.project.track_by_id(first.id) is not original_tracks[first.id])
    assert engine.project.track_by_id(first.id) is not original_tracks[first.id]
    snapshot = session.prepared_render_snapshot(first.id)
    assert snapshot is not None
    assert np.shares_memory(engine.project.track_by_id(first.id).playback_samples, snapshot.samples)
    assert engine.project.track_by_id(second.id) is original_tracks[second.id]

    rendered.clear()
    third = session.add_audio_track(np.full(8, 0.4, dtype=np.float32), name="c", identity="c")
    assert rendered == []
    assert engine.project.track_by_id(second.id) is original_tracks[second.id]

    rendered.clear()
    session.remove_track(second.id)
    assert rendered == []
    assert [track.id for track in engine.project.tracks] == [first.id, third.id]
    engine.close()


def test_navigation_events_never_render_audio(monkeypatch) -> None:
    session = EditorSession(sample_rate=1_000)
    track = session.add_audio_track(
        np.zeros(10_000, dtype=np.float32), name="fixture", identity="fixture"
    )
    engine = SessionAudioEngine(session, FakeOutputBackend())
    rendered = []
    monkeypatch.setattr(session, "render_snapshot", rendered.append)

    session.set_viewport(1_000, 4_000)
    session.pan_viewport(250)
    session.zoom_viewport(0.5, 2_000)
    session.set_playhead(1_500)
    session.set_selection(1_000, 2_000)

    assert engine.project.track_by_id(track.id).id == track.id
    assert rendered == []
    engine.close()


def test_selecting_other_track_updates_project_active_track_and_selection() -> None:
    session = EditorSession(sample_rate=10)
    first = session.add_audio_track(np.ones(10), name="first", identity="first")
    second = session.add_audio_track(np.ones(10), name="second", identity="second")
    audio = SessionAudioEngine(session, backend=FakeOutputBackend())

    session.set_active_track(first.id)
    session.set_selection(2, 6)

    assert audio.project.active_track_id == first.id
    assert audio.project.selection_for(first.id).active
    assert not audio.project.selection_for(second.id).active
    audio.close()


@pytest.mark.parametrize("operation", ["cut_selection", "delete_selection"])
def test_destructive_edits_clear_audio_selection_and_undo_restores_it(operation) -> None:
    session = EditorSession(sample_rate=10)
    track = session.add_audio_track(np.ones(10), name="track", identity="track")
    audio = SessionAudioEngine(session, backend=FakeOutputBackend())
    session.set_selection(2, 5)

    getattr(session, operation)()
    assert not audio.project.selection_for(track.id).active
    session.commands.undo()
    assert audio.project.selection_for(track.id).active
    session.commands.redo()
    assert not audio.project.selection_for(track.id).active
    assert audio.renders.wait(5_000)
    audio.close()


def test_paste_selection_is_mirrored_to_audio_project() -> None:
    session = EditorSession(sample_rate=10)
    track = session.add_audio_track(np.ones(10), name="track", identity="track")
    audio = SessionAudioEngine(session, backend=FakeOutputBackend())
    session.set_selection(2, 5)
    session.copy_selection()
    session.set_playhead(6)

    session.paste()

    selection = audio.project.selection_for(track.id)
    assert (selection.start, selection.end) == (0.6, 0.9)
    assert audio.renders.wait(5_000)
    audio.close()
