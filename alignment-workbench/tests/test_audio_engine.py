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
