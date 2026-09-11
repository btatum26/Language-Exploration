from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf
from PySide6 import QtCore, QtGui, QtMultimedia, QtWidgets

from application import CreateRecordingCommand
from gui.tasks import TaskRunner
from gui.track_stack import STANDARD_TRACK_HEIGHT
from gui.workspace import Track, WorkspaceController
from gui.workspace_player import mix_block
from gui.workstation_window import WorkstationWindow
from tests.test_application_api import MemoryPersistenceStore
from tests.test_core_library import make_api
from tests.test_first_use_gui import drag
from tests.test_gui import ImmediateRunner


def test_offsets_selection_order_and_mix(qapp):
    workspace = WorkspaceController()
    a = Track(uuid4(), Path("a.wav"), 2, 8000)
    b = Track(uuid4(), Path("b.wav"), 3, 16000)
    assert workspace.add(a) and workspace.add(b)
    assert not workspace.add(a)
    assert workspace.active_id == a.recording_id
    workspace.select((4000, 8000))
    workspace.place(a.recording_id, 2)
    assert workspace.selection_times == (2.5, 3)
    assert a.local_sample(2.5) == 4000
    assert a.local_sample(0) == 0
    assert a.local_sample(100) == 16000
    assert workspace.duration == 4
    workspace.set_mix(b.recording_id, muted=False, solo=True)
    assert workspace.audible_tracks == (b,)
    workspace.set_mix(b.recording_id, muted=True, solo=True)
    assert workspace.audible_tracks == ()
    workspace.place(a.recording_id, -1)
    assert a.offset == 0
    workspace.activate(b.recording_id)
    assert workspace.selection is None
    workspace.move_order(b.recording_id, -1)
    assert workspace.tracks == [b, a]
    workspace.remove(b.recording_id)
    assert workspace.active_id == a.recording_id


def test_mixer_offsets_channels_rates_and_silence(tmp_path, qapp):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    sf.write(a, np.full(8000, 0.125), 8000, subtype="FLOAT")
    sf.write(b, np.tile([0.25, -0.25], (16000, 1)), 16000, subtype="FLOAT")
    workspace = WorkspaceController()
    first = Track(uuid4(), a, 1, 8000)
    second = Track(uuid4(), b, 1, 16000, offset=0.5)
    workspace.add(first)
    workspace.add(second)

    def block(seconds):
        return np.frombuffer(
            mix_block(workspace.audible_tracks, round(seconds * 48000), 128, 48000), dtype="<f4"
        ).reshape(-1, 2)

    assert np.allclose(block(0), [0.125, 0.125])
    assert np.allclose(block(0.6), [0.375, -0.125])
    assert np.allclose(block(1.2), [0.25, -0.25])
    assert not block(2).any()
    workspace.set_mix(second.recording_id, muted=False, solo=True)
    assert not block(0).any()
    assert np.allclose(block(0.6), [0.25, -0.25])
    workspace.set_mix(second.recording_id, muted=True, solo=True)
    assert not block(0.6).any()


def workstation(qtbot, tmp_path, *, runner=None, store=None):
    api = make_api(store or MemoryPersistenceStore(), tmp_path)
    api.ensure_bundled_libraries()
    for index, rate in enumerate((8000, 16000)):
        source = tmp_path / f"voice-{index}.wav"
        time = np.arange(rate * 3) / rate
        sf.write(source, 0.2 * np.sin(2 * np.pi * (220 + 110 * index) * time), rate)
        session = api.import_recording(
            CreateRecordingCommand(
                recording_id=uuid4(),
                source_audio_path=source,
                name=f"Voice {index + 1}",
                language="en",
            )
        )
        session.close()
    window = WorkstationWindow(api, task_runner=runner or ImmediateRunner())
    qtbot.addWidget(window, before_close_func=lambda w: w.shutdown())
    window.show()
    for item in api.list_recordings():
        window.add_recording(item)
    qtbot.waitUntil(lambda: len(window.tracks) == 2)
    qtbot.waitUntil(lambda: all(w.editor.waveform.audio_ready for w in window.tracks.values()))
    return api, window


def test_two_track_inspector_and_alignment(qtbot, tmp_path):
    api, window = workstation(qtbot, tmp_path)
    a, b = window.tracks.values()
    editor = a.editor
    window.workspace.activate(a.track.recording_id)
    assert window.inspector.currentWidget() is window.empty
    b.editor.audio_view_toggle.setCurrentIndex(1)
    qtbot.waitUntil(lambda: b.editor.waveform.spectrogram is not None)
    assert editor.concept_combo.count() == 39
    assert not editor.controller.session.dirty
    editor.concept_search.setText("epsilon")
    drag(editor.waveform, 4000, 10000)
    assert window.inspector.currentWidget() is editor.editor_scroll
    editor.label_edit.setText("Vowel")
    editor.create_annotation_button.click()
    session = editor.controller.session
    assert len(session.annotations) == 1
    original = session.annotations[0]
    assert str(original.concept_ref) == "phonetics@0.1:epsilon"
    assert editor._selected_annotation_id == original.id
    editor.label_edit.setText("Edited vowel")
    editor.update_annotation_button.click()
    assert session.annotations[0].label == "Edited vowel"
    editor.label_edit.setText("Unapplied draft")
    editor.inspector_binding.revert.click()
    assert editor.label_edit.text() == "Edited vowel"
    window.workspace.place(a.track.recording_id, 1)
    assert session.annotations[0].geometry == original.geometry
    assert editor.waveform.sample_to_x(4000) == pytest.approx(
        b.editor.waveform.sample_to_x(24000), abs=1
    )
    window.workspace.set_viewport(0.5, 2.5)
    assert a.editor.waveform.viewport == (-4000, 12000)
    assert b.editor.waveform.viewport == (8000, 40000)
    window.player.seek(1.5)
    assert editor.waveform._playhead_seconds == 0.5
    assert b.editor.waveform._playhead_seconds == 1.5
    editor.waveform.grab()
    rect = editor.waveform._annotation_rects[original.id]
    qtbot.mouseClick(editor.waveform, QtCore.Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert editor._selected_annotation_id == original.id
    editor.delete_annotation_button.click()
    assert session.annotations == ()
    editor.controller.undo()
    assert len(session.annotations) == 1
    session.save()
    window.workspace.fit()
    window.workspace.activate(a.track.recording_id)
    editor._select_annotation(original.id)
    window._active(a.track.recording_id)
    qtbot.wait(200)
    artifact = Path(".artifacts/workstation")
    artifact.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(artifact / "two-tracks.png"))
    assert not editor.recordings_list.isVisible()
    assert not editor.libraries_list.isVisible()
    assert not editor.annotation_table.isVisible()
    window.remove_track(a.track.recording_id)
    assert len(window.tracks) == 1
    reopened = api.open_recording(a.track.recording_id)
    assert len(reopened.annotations) == 1
    reopened.close()


def test_close_saves_all_tracks_and_cancel_keeps_workspace(qtbot, tmp_path, monkeypatch):
    _, window = workstation(qtbot, tmp_path)
    for widget in window.tracks.values():
        widget.editor.controller.use_core_library()
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda _: QtWidgets.QDialog.DialogCode.Rejected)
    window.close()
    assert window.isVisible()
    assert len(window.tracks) == 2
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda _: QtWidgets.QDialog.DialogCode.Accepted)
    window.close()
    qtbot.waitUntil(lambda: window._shutdown)
    assert all(w.editor.controller.session is None for w in window.tracks.values())


def test_async_workers_clip_drag_overlap_and_marker(qtbot, tmp_path):
    runner = TaskRunner()
    _, window = workstation(qtbot, tmp_path, runner=runner)
    qtbot.waitUntil(lambda: all(not w.editor.controller.busy for w in window.tracks.values()))
    a, b = window.tracks.values()
    header = a.clip_header
    qtbot.mousePress(header, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(20, 12))
    qtbot.mouseMove(header, QtCore.QPoint(180, 12))
    qtbot.mouseRelease(header, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(180, 12))
    assert a.track.offset > 0.4
    editor = a.editor
    editor.concept_search.setText("silence")
    drag(editor.waveform, 3000, 9000)
    editor.create_annotation_button.click()
    editor._new_annotation()
    drag(editor.waveform, 4000, 8000)
    editor.create_annotation_button.click()
    assert len(editor.controller.session.annotations) == 2
    editor.waveform.grab()
    rects = list(editor.waveform._annotation_rects.values())
    assert not rects[0].intersects(rects[1])
    for annotation in editor.controller.session.annotations:
        rect = editor.waveform._annotation_rects[annotation.id]
        qtbot.mouseClick(
            editor.waveform, QtCore.Qt.MouseButton.LeftButton, pos=rect.center().toPoint()
        )
        assert editor._selected_annotation_id == annotation.id
    editor.inspector_binding.start.setValue(0.6)
    editor.update_annotation_button.click()
    assert (
        editor.controller.session.get_annotation(
            editor._selected_annotation_id
        ).geometry.start_sample
        == 4800
    )
    a.create_marker()
    assert window.inspector.currentWidget() is editor.editor_scroll
    editor.create_annotation_button.click()
    assert any(a.geometry.type == "point" for a in editor.controller.session.annotations)
    editor.waveform.zoom(0.5)
    start, end = window.workspace.viewport
    assert b.editor.waveform.viewport == (round(start * 16000), round(end * 16000))
    window.shutdown()
    assert runner.shutdown()


def test_real_output_play_pause_seek_stop(qtbot, tmp_path):
    if QtMultimedia.QMediaDevices.defaultAudioOutput().isNull():
        pytest.skip("No output device")
    _, window = workstation(qtbot, tmp_path)
    errors = []
    window.player.error.connect(errors.append)
    window.player.play()
    assert not errors

    qtbot.waitUntil(lambda: window.workspace.playhead > 0.1)
    a, b = window.tracks.values()
    assert a.editor.waveform._playhead_seconds == b.editor.waveform._playhead_seconds
    window.player.pause()
    paused = window.workspace.playhead
    qtbot.wait(80)
    assert window.workspace.playhead == paused
    window.player.seek(1)
    window.player.play()
    qtbot.waitUntil(lambda: window.workspace.playhead > 1.1)
    window.player.stop()
    assert window.workspace.playhead == 0
    assert not errors


def test_multitrack_offline_save_recovery_preserves_offset(qtbot, tmp_path, monkeypatch):
    store = MemoryPersistenceStore()
    api, window = workstation(qtbot, tmp_path, store=store)
    a, b = window.tracks.values()
    window.workspace.place(a.track.recording_id, 1.25)
    a.editor.controller.session.set_name("Offline edit")
    store.offline = True
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda _: QtWidgets.QDialog.DialogCode.Accepted)
    window.close()
    assert not window._closing and window.isVisible()
    assert len(api.recovery.list_pending()) == 1
    assert len(window.tracks) == 2
    store.offline = False
    window.retry()
    qtbot.waitUntil(lambda: not window._retrying)
    assert not api.recovery.list_pending()
    assert a.editor.controller.session.name == "Offline edit"
    assert a.track.offset == 1.25
    assert b.editor.controller.session is not None
    window.close()
    qtbot.waitUntil(lambda: window._shutdown)


def test_spectrogram_requested_before_waveform_ready(qtbot, tmp_path):
    _, window = workstation(qtbot, tmp_path)
    editor = next(iter(window.tracks.values())).editor
    envelope = editor.waveform._envelope
    editor.waveform.set_loading()
    editor.audio_view_toggle.setCurrentIndex(1)
    qtbot.wait(160)
    assert editor.waveform.spectrogram is None
    editor.waveform.set_recording(envelope, editor.controller.session.annotations)
    qtbot.waitUntil(lambda: editor.waveform.spectrogram is not None)


def test_track_heights_resize_and_vertical_overflow(qtbot, tmp_path):
    _, window = workstation(qtbot, tmp_path)
    a, b = window.tracks.values()
    assert a.height() == b.height() == STANDARD_TRACK_HEIGHT
    window.resize(1200, 500)
    bar = window.track_scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    assert a.height() == b.height() == STANDARD_TRACK_HEIGHT
    viewport = window.workspace.viewport
    view = a.editor.waveform
    point = QtCore.QPointF(80, 100)
    wheel = QtGui.QWheelEvent(
        point,
        view.mapToGlobal(point.toPoint()),
        QtCore.QPoint(),
        QtCore.QPoint(0, -120),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QtWidgets.QApplication.sendEvent(view, wheel)
    assert bar.value() > 0
    assert window.workspace.viewport == viewport
    bar.setValue(0)
    handle = a.resize_handle
    qtbot.mousePress(handle, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(20, 3))
    qtbot.mouseMove(handle, QtCore.QPoint(20, 65))
    qtbot.mouseRelease(handle, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(20, 3))
    resized = a.height()
    assert resized > STANDARD_TRACK_HEIGHT
    assert b.height() == STANDARD_TRACK_HEIGHT
    window.workspace.move_order(a.track.recording_id, 1)
    window.workspace.set_mix(a.track.recording_id, muted=True, solo=False)
    window.resize(1300, 550)
    qtbot.wait(50)
    assert a.height() == resized and b.height() == STANDARD_TRACK_HEIGHT
    bar.setValue(bar.maximum())
    assert a.resize_handle.mapTo(window.track_scroll.viewport(), QtCore.QPoint()).y() >= 0
