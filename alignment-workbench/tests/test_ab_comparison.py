from __future__ import annotations

import numpy as np
from spectrogram_playground.audio.engine import FakeOutputBackend

from alignment_workbench.audio.engine import SessionAudioEngine
from alignment_workbench.state.editor import EditorSession, Segment
from alignment_workbench.ui.ab_comparison import ABComparisonController, corresponding_segment


def segment(identifier: str, label: str, start: int, end: int, **values: object) -> Segment:
    return Segment(
        id=identifier,
        kind=str(values.pop("kind", "word")),
        label=label,
        start_frame=start,
        end_frame=end,
        parent_id=values.pop("parent_id", None),  # type: ignore[arg-type]
        provenance=values,
    )


def comparison_scene():
    session = EditorSession(sample_rate=1_000)
    practice_segment = segment("practice-word", "ciao", 100, 300, source_token_ids=("t1",))
    reference_segment = segment("reference-word", "ciao", 500, 900, source_token_ids=("t1",))
    practice = session.add_audio_track(
        np.zeros(1_000, dtype=np.float32),
        name="practice",
        identity="practice",
        words=[practice_segment],
    )
    reference = session.add_audio_track(
        np.zeros(1_000, dtype=np.float32),
        name="reference",
        identity="reference",
        words=[reference_segment],
    )
    audio = SessionAudioEngine(session, backend=FakeOutputBackend())
    controller = ABComparisonController(session, audio)
    return session, audio, controller, practice, reference, practice_segment, reference_segment


def test_practice_then_reference_uses_each_tracks_own_interval_and_restores_state() -> None:
    session, audio, controller, practice, reference, first, second = comparison_scene()
    session.set_track_mix(practice.id, gain=0.7, muted=True, solo=False)
    session.set_track_mix(reference.id, gain=1.3, muted=False, solo=True)
    session.set_active_track(reference.id)
    session.set_selection(20, 80)
    session.set_playhead(40)
    audio.set_loop(True)

    controller.start(practice.id, first, reference.id, second)
    assert session.active_track_id == practice.id
    assert (session.selection.start, session.selection.end) == (100, 300)
    controller._advance()
    assert session.active_track_id == reference.id
    assert (session.selection.start, session.selection.end) == (500, 900)
    controller._advance()

    assert session.active_track_id == reference.id
    assert (session.selection.start, session.selection.end) == (20, 80)
    assert session.playhead_frame == 40
    assert audio.project.transport.loop_selection
    assert (practice.gain, practice.muted, practice.solo) == (0.7, True, False)
    assert (reference.gain, reference.muted, reference.solo) == (1.3, False, True)
    controller.close()
    audio.close()


def test_extra_silence_does_not_shift_phone_correspondence() -> None:
    session = EditorSession(sample_rate=1_000)
    source_word = segment("sw", "ciao", 0, 400)
    target_word = segment("tw", "ciao", 0, 500)
    source_phone = segment("sp2", "a", 200, 400, kind="phone", parent_id="sw")
    source = session.add_audio_track(
        np.zeros(500, dtype=np.float32),
        name="practice",
        identity="practice",
        words=[source_word],
        phones=[
            segment("sp1", "k", 0, 200, kind="phone", parent_id="sw"),
            source_phone,
        ],
    )
    target = session.add_audio_track(
        np.zeros(500, dtype=np.float32),
        name="reference",
        identity="reference",
        words=[target_word],
        phones=[
            segment("sil", "sil", 0, 50, kind="silence", parent_id="tw"),
            segment("tp1", "k", 50, 250, kind="phone", parent_id="tw"),
            segment("tp2", "a", 250, 500, kind="phone", parent_id="tw"),
        ],
    )

    match = corresponding_segment(source, target, source_phone)

    assert match is not None
    assert match.id == "tp2"


def test_second_comparison_and_track_removal_invalidate_pending_callbacks(monkeypatch) -> None:
    session, audio, controller, practice, reference, first, second = comparison_scene()
    renders: list[object] = []
    monkeypatch.setattr(session, "render_track", lambda track_id: renders.append(track_id))

    controller.start(practice.id, first, reference.id, second)
    controller.start(reference.id, second, practice.id, first)
    generation = controller._generation
    session.remove_track(practice.id)

    assert controller._state is None
    assert controller._generation > generation
    controller._advance()
    assert renders == []
    controller.close()
    audio.close()
