from __future__ import annotations

import numpy as np
from src.audio.engine import FakeOutputBackend

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


def test_track_events_update_audio_project_without_full_renders(monkeypatch) -> None:
    session = EditorSession(sample_rate=8)
    first = session.add_audio_track(np.full(8, 0.2, dtype=np.float32), name="a", identity="a")
    second = session.add_audio_track(np.full(8, 0.3, dtype=np.float32), name="b", identity="b")
    backend = FakeOutputBackend()
    engine = SessionAudioEngine(session, backend)
    original_tracks = {track.id: track for track in engine.project.tracks}
    original_render = session.render_track
    rendered = []

    def record_render(track_id):
        rendered.append(track_id)
        return original_render(track_id)

    monkeypatch.setattr(session, "render_track", record_render)

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

    session.active_track_id = first.id
    session.split_at(4)
    assert rendered == [first.id]
    assert engine.project.track_by_id(first.id) is not original_tracks[first.id]
    assert engine.project.track_by_id(second.id) is original_tracks[second.id]

    rendered.clear()
    third = session.add_audio_track(np.full(8, 0.4, dtype=np.float32), name="c", identity="c")
    assert rendered == [third.id]
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
    monkeypatch.setattr(session, "render_track", rendered.append)

    session.set_viewport(1_000, 4_000)
    session.pan_viewport(250)
    session.zoom_viewport(0.5, 2_000)
    session.set_playhead(1_500)
    session.set_selection(1_000, 2_000)

    assert engine.project.track_by_id(track.id).id == track.id
    assert rendered == []
    engine.close()
