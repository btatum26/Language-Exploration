from __future__ import annotations

import threading

import numpy as np
import soundfile as sf

from alignment_workbench.audio.rendering import write_snapshot_to_wav
from alignment_workbench.state.editor import EditorSession


def test_same_length_content_replacement_gets_new_render_identity() -> None:
    session = EditorSession(sample_rate=10)
    track = session.add_audio_track(
        np.arange(10, dtype=np.float32) / 10, name="track", identity="source"
    )
    first = session.render_snapshot(track.id)
    session.set_selection(2, 4)
    session.copy_selection()
    session.delete_selection()
    session.paste(2)
    second = session.render_snapshot(track.id)

    assert len(first.samples) == len(second.samples)
    assert second.content_version > first.content_version
    assert second is session.render_snapshot(track.id)


def test_export_uses_immutable_snapshot_while_editor_changes(tmp_path) -> None:
    session = EditorSession(sample_rate=10)
    track = session.add_audio_track(
        np.linspace(-0.5, 0.5, 10, dtype=np.float32), name="track", identity="source"
    )
    snapshot = session.render_snapshot(track.id)
    session.set_selection(2, 4)
    session.delete_selection()
    destination = tmp_path / "export.wav"

    write_snapshot_to_wav(snapshot.samples, session.sample_rate, destination)
    exported, rate = sf.read(destination, dtype="float32")

    assert rate == session.sample_rate
    np.testing.assert_allclose(exported, snapshot.samples, atol=1 / 2**15)


def test_cancelled_export_preserves_existing_destination(tmp_path) -> None:
    destination = tmp_path / "export.wav"
    destination.write_bytes(b"existing")
    cancel = threading.Event()
    cancel.set()

    try:
        write_snapshot_to_wav(np.zeros(10, dtype=np.float32), 10, destination, cancel)
    except RuntimeError as exc:
        assert "cancelled" in str(exc)
    else:
        raise AssertionError("cancelled export must fail")

    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [destination]


def test_failed_export_preserves_existing_destination(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "export.wav"
    destination.write_bytes(b"existing")

    def fail_after_partial_write(path, *_args, **_kwargs):
        path.write_bytes(b"partial")
        raise RuntimeError("encoder failed")

    monkeypatch.setattr("alignment_workbench.audio.rendering.sf.write", fail_after_partial_write)

    try:
        write_snapshot_to_wav(np.zeros(10, dtype=np.float32), 10, destination)
    except RuntimeError as exc:
        assert str(exc) == "encoder failed"
    else:
        raise AssertionError("failed export must propagate its encoder error")

    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [destination]
