from pathlib import Path
from uuid import uuid4

import pytest
from PySide6 import QtCore, QtGui, QtMultimedia, QtTest, QtWidgets

from application import CreateRecordingCommand
from gui.main_window import MainWindow
from gui.waveform import load_waveform_detail, load_waveform_envelope
from tests.test_application_api import MemoryPersistenceStore
from tests.test_core_library import make_api
from tests.test_gui import DeferredRunner, ImmediateRunner, _write_wav


@pytest.fixture(autouse=True)
def discard_on_test_teardown(monkeypatch, qapp):
    qapp.setFont(QtGui.QFont("Segoe UI", 9))
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *a, **kw: QtWidgets.QMessageBox.StandardButton.Discard,
    )


def drag(view, start, end, *, lane=False):
    y = 25 if lane else 105
    first = QtCore.QPoint(round(view.sample_to_x(start)), y)
    last = QtCore.QPoint(round(view.sample_to_x(end)), y)
    QtTest.QTest.mousePress(view, QtCore.Qt.MouseButton.LeftButton, pos=first)
    QtTest.QTest.mouseMove(view, last)
    QtTest.QTest.mouseRelease(view, QtCore.Qt.MouseButton.LeftButton, pos=last)


def click_editor(qtbot, window, widget):
    QtWidgets.QApplication.processEvents()
    window.editor_scroll.ensureWidgetVisible(widget)
    QtWidgets.QApplication.processEvents()
    center = widget.mapTo(window.editor_scroll.viewport(), widget.rect().center())
    assert window.editor_scroll.viewport().rect().contains(center)
    assert widget.height() >= widget.minimumSizeHint().height()
    assert widget.isEnabled() and widget.isVisible(), (
        widget.text(),
        window._selected_annotation_id,
    )
    qtbot.mouseClick(widget, QtCore.Qt.MouseButton.LeftButton)


def exercise_first_use(qtbot, api, tmp_path, monkeypatch, *, size=(1024, 768)):
    """Same real GUI workflow is exercised with memory and PostgreSQL stores."""
    core = api.ensure_core_library()
    QtWidgets.QApplication.instance().setFont(QtGui.QFont("Segoe UI", 9))
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.resize(*size)
    window.show()
    source = tmp_path / "manual.wav"
    _write_wav(source)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(source), "WAV")
    )
    answers = iter((("Manual first use", True), ("en", True)))
    monkeypatch.setattr(QtWidgets.QInputDialog, "getText", lambda *a, **kw: next(answers))
    window.import_action.trigger()
    qtbot.waitUntil(lambda: window.waveform._envelope is not None)
    session = window.controller.session
    assert session is not None
    assert not session.dirty
    assert session.pinned_libraries == (core,)
    assert window.controller.core_is_pinned
    assert str(window.controller.available_concepts[0].reference) == "core@0.1:test"
    assert "core@0.1" in window.concept_combo.currentText()
    assert window.geometry_combo.currentData() == "time_interval"
    assert not window.minimum_frequency.isVisible()
    assert not window.create_annotation_button.isEnabled()
    seek_events = []
    window.waveform.seek_requested.connect(seek_events.append)
    drag(window.waveform, 500, 100)
    assert seek_events == []
    selected = window.waveform.selection
    assert selected is not None and selected[0] < selected[1]
    window.note_edit.setText("first annotation")
    window.label_edit.setText("First interval")
    click_editor(qtbot, window, window.create_annotation_button)
    (annotation,) = session.annotations
    assert (annotation.geometry.start_sample, annotation.geometry.end_sample) == selected
    assert window._selected_annotation_id == annotation.id
    assert window.annotation_table.currentRow() == 0
    assert window.waveform._selected_annotation_id == annotation.id
    assert not window.geometry_combo.isEnabled()
    assert window.geometry_combo.currentData() == annotation.geometry.type
    assert annotation.label == "First interval"
    assert window.annotation_table.item(0, 6).text() == "First interval"
    source_before = window._player.source()
    source_changes = []
    window._player.sourceChanged.connect(source_changes.append)
    window.start_sample.setValue(120)
    window.end_sample.setValue(550)
    window.note_edit.setText("saved note")
    window.label_edit.setText("Edited interval")
    click_editor(qtbot, window, window.update_annotation_button)
    edited = session.annotations[0]
    assert edited.geometry.start_sample == 120
    assert edited.geometry.end_sample == 550
    assert edited.note == "saved note"
    assert edited.label == "Edited interval"
    window.undo_action.trigger()
    assert session.annotations == (annotation,)
    assert window.note_edit.text() == "first annotation"
    assert window.label_edit.text() == "First interval"
    assert window.annotation_table.item(0, 6).text() == "First interval"
    window.redo_action.trigger()
    assert session.annotations == (edited,)
    assert window.end_sample.value() == 550
    assert window.label_edit.text() == "Edited interval"
    assert window.annotation_table.item(0, 6).text() == "Edited interval"
    qtbot.wait(10)
    drag(window.waveform, 550, 680, lane=True)
    dragged = session.annotations[0]
    assert abs(dragged.geometry.end_sample - 680) <= 1
    window.undo_action.trigger()
    assert session.annotations == (edited,)
    window.redo_action.trigger()
    assert session.annotations == (dragged,)
    assert dragged.label == "Edited interval"
    assert window.end_sample.value() == dragged.geometry.end_sample
    # Escape cancels a boundary preview without changing the edit history.
    edge = QtCore.QPoint(round(window.waveform.sample_to_x(dragged.geometry.end_sample)), 25)
    qtbot.mousePress(window.waveform, QtCore.Qt.MouseButton.LeftButton, pos=edge)
    qtbot.mouseMove(window.waveform, edge - QtCore.QPoint(50, 0))
    qtbot.keyClick(window.waveform, QtCore.Qt.Key.Key_Escape)
    qtbot.mouseRelease(window.waveform, QtCore.Qt.MouseButton.LeftButton, pos=edge)
    assert session.annotations == (dragged,) and window.waveform._preview is None
    assert window._selected_annotation_id == dragged.id
    # Invalid crossing restores the authoritative geometry and adds no undo entry.
    drag(window.waveform, dragged.geometry.end_sample, 60, lane=True)
    assert session.annotations == (dragged,)
    assert window.waveform._preview is None
    window.undo_action.trigger()
    assert session.annotations == (edited,)
    window.redo_action.trigger()
    assert session.annotations == (dragged,)
    click_editor(qtbot, window, window.delete_annotation_button)
    assert session.annotations == ()
    assert window.annotation_table.rowCount() == 0
    assert window.annotation_details.toPlainText() == ""
    window.undo_action.trigger()
    assert session.annotations == (dragged,)
    window.redo_action.trigger()
    assert session.annotations == ()
    window.undo_action.trigger()
    window.annotation_table.selectRow(0)
    assert window._selected_annotation_id == dragged.id
    assert str(dragged.id) in window.annotation_details.toPlainText()
    assert source_changes == [] and window._player.source() == source_before
    window.error_banner.hide()
    # Render ordinary desktop sizes; scroll to every editable control and assert accessibility.
    for widget in (
        window.label_edit,
        window.concept_combo,
        window.start_sample,
        window.end_sample,
        window.note_edit,
        window.update_annotation_button,
        window.pinned_libraries,
    ):
        window.editor_scroll.ensureWidgetVisible(widget)
        QtWidgets.QApplication.processEvents()
        center = widget.mapTo(window.editor_scroll.viewport(), widget.rect().center())
        assert window.editor_scroll.viewport().rect().contains(center)
    window.editor_scroll.verticalScrollBar().setValue(0)
    artifacts = Path(".artifacts/first-use")
    artifacts.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(artifacts / f"editor-{size[0]}x{size[1]}.png"))
    QtCore.QTimer.singleShot(0, window.save_dialog.accept)
    window.save_action.trigger()
    assert not session.dirty
    assert "clean" in window.session_state_value.text()
    recording_id = session.recording_id
    window.close()
    return recording_id, dragged, core


@pytest.mark.parametrize("size", [(1024, 768), (1340, 850)])
def test_real_session_first_use(qtbot, tmp_path, monkeypatch, size):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    recording_id, annotation, core = exercise_first_use(
        qtbot, api, tmp_path, monkeypatch, size=size
    )
    reopened_api = make_api(store, tmp_path)
    assert reopened_api.ensure_core_library() == core
    reopened = reopened_api.open_recording(recording_id)
    assert reopened.annotations == (annotation,)
    assert reopened.pinned_libraries == (core,)
    snapshot = store.load_snapshot(recording_id)
    (pin,) = snapshot.libraries
    assert (pin.namespace, pin.version, pin.content_sha256) == ("core", "0.1", core.content_sha256)


def test_existing_recording_requires_explicit_pin_and_undo_stays_unpinned(qtbot, tmp_path):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    core = api.ensure_core_library()
    source = tmp_path / "old.wav"
    _write_wav(source)
    session = api.import_recording(
        CreateRecordingCommand(
            recording_id=uuid4(), source_audio_path=source, name="Old", language="en"
        )
    )
    session.close()
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.show()
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    qtbot.waitUntil(lambda: window.waveform._envelope is not None)
    session = window.controller.session
    assert session.pinned_libraries == () and not session.dirty
    assert "No pinned libraries" in window.concept_status.text()
    click_editor(qtbot, window, window.use_core_button)
    assert session.pinned_libraries == (core,) and session.dirty
    assert store.load_snapshot(session.recording_id).libraries == ()
    window.undo_action.trigger()
    window.controller.refresh_all()
    assert session.pinned_libraries == ()
    window.redo_action.trigger()
    QtCore.QTimer.singleShot(0, window.save_dialog.accept)
    window.save_action.trigger()
    assert store.load_snapshot(session.recording_id).libraries[0].version == "0.1"
    # Saving disables gestures and direct controller mutations until completion.
    deferred = DeferredRunner()
    window.controller._task_runner = deferred
    window.controller.save()
    before = session.annotations
    drag(window.waveform, 100, 400)
    window.controller.undo()
    assert session.annotations == before and session.pinned_libraries == (core,)
    deferred.complete(0)
    deferred.complete(0)  # catalog refresh requested by save completion
    window.shutdown()


def test_waveform_seek_zoom_pan_cancel_and_clamp(qtbot, tmp_path):
    from gui.waveform import WaveformView

    source = tmp_path / "wave.wav"
    _write_wav(source)
    view = WaveformView()
    qtbot.addWidget(view)
    view.resize(820, 210)
    view.show()
    view.set_recording(load_waveform_envelope(source), ())
    seek_events = []
    view.seek_requested.connect(seek_events.append)
    qtbot.mouseClick(view, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(210, 105))
    assert seek_events == [200]
    drag(view, 650, -150)
    assert view.selection == (0, 650)
    assert seek_events == [200]
    view.zoom_to_selection()
    assert view.viewport == (0, 650)
    view.set_viewport(200, 400)
    assert view.x_to_sample(view.sample_to_x(300)) == 300
    qtbot.mousePress(view, QtCore.Qt.MouseButton.MiddleButton, pos=QtCore.QPoint(410, 105))
    qtbot.mouseMove(view, QtCore.QPoint(210, 105))
    qtbot.mouseRelease(view, QtCore.Qt.MouseButton.MiddleButton, pos=QtCore.QPoint(210, 105))
    assert view.viewport == (250, 450)
    before = view.selection
    qtbot.mousePress(view, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(210, 105))
    qtbot.mouseMove(view, QtCore.QPoint(410, 105))
    qtbot.keyClick(view, QtCore.Qt.Key.Key_Escape)
    qtbot.mouseRelease(view, QtCore.Qt.MouseButton.LeftButton, pos=QtCore.QPoint(410, 105))
    assert view.selection == before
    view.clear_selection()
    assert view.selection is None
    view.fit_recording()
    assert view.viewport == (0, 800)
    detail = load_waveform_detail(source, 100, 120, point_count=2000)
    assert detail.start_sample == 100 and detail.end_sample == 120
    assert len(detail.minimum) == 20


def test_playback_diagnostics_survive_annotation_notices(qtbot, tmp_path, monkeypatch):
    api = make_api(MemoryPersistenceStore(), tmp_path)
    api.ensure_core_library()
    monkeypatch.setattr(QtMultimedia.QMediaDevices, "audioOutputs", lambda: [])
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    assert "No audio output device" in window.playback_status.text()
    assert not window.play_pause_button.isEnabled()
    window._playback_error(QtMultimedia.QMediaPlayer.Error.ResourceError, "device failure")
    window._show_notice("Saved revision 2")
    assert "device failure" in window.playback_status.text()
    assert not window.play_pause_button.isEnabled()
    window.shutdown()


def test_available_device_play_pause_seek_and_stop(qtbot, tmp_path):
    from tests.test_application_api import write_wav

    if not QtMultimedia.QMediaDevices.audioOutputs():
        pytest.skip("No audio output device is available for the playback smoke test")
    api = make_api(MemoryPersistenceStore(), tmp_path)
    api.ensure_core_library()
    source = tmp_path / "playback.wav"
    write_wav(source, frame_count=32_000)
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.show()
    window.controller.import_recording(source, name="Playback", language="en")
    qtbot.waitUntil(lambda: window.play_pause_button.isEnabled(), timeout=10_000)
    assert window._player.isSeekable()
    qtbot.mouseClick(window.play_pause_button, QtCore.Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window._player.position() > 0, timeout=5_000)
    assert window._player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
    qtbot.mouseClick(window.play_pause_button, QtCore.Qt.MouseButton.LeftButton)
    assert window._player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PausedState
    window.waveform.seek_requested.emit(8_000)
    qtbot.waitUntil(lambda: abs(window._player.position() - 1_000) < 50)
    assert abs(window.waveform._playhead_seconds - 1) < 0.05
    qtbot.mouseClick(window.stop_button, QtCore.Qt.MouseButton.LeftButton)
    assert window._player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.StoppedState
    assert window._player.position() == 0
    assert window.waveform._playhead_seconds == 0
    window.close()


def test_polygon_is_preserved_and_existing_geometry_is_locked(qtbot, tmp_path):
    from models import (
        ConceptRef,
        SignalAnnotation,
        TimeFrequencyPolygonGeometry,
        TimeFrequencyVertex,
    )
    from tests.test_application_api import publish_test_library

    api = make_api(MemoryPersistenceStore(), tmp_path)
    api.ensure_core_library()
    _, pin = publish_test_library(api)
    source = tmp_path / "polygon.wav"
    _write_wav(source)
    polygon = SignalAnnotation(
        id=uuid4(),
        concept_ref=ConceptRef("le.test@1.0.0:event"),
        attributes={"label": "preserve this"},
        note="polygon note",
        geometry=TimeFrequencyPolygonGeometry(
            start_sample=100,
            end_sample=300,
            min_frequency_hz=100,
            max_frequency_hz=300,
            vertices=(
                TimeFrequencyVertex(sample=100, frequency_hz=100),
                TimeFrequencyVertex(sample=300, frequency_hz=100),
                TimeFrequencyVertex(sample=200, frequency_hz=300),
            ),
        ),
    )
    original = api.import_recording(
        CreateRecordingCommand(
            recording_id=uuid4(),
            source_audio_path=source,
            name="Polygon",
            language="en",
            libraries=(pin,),
            annotations=(polygon,),
        )
    )
    original.close()
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.show()
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    qtbot.waitUntil(lambda: window.waveform._envelope is not None)
    window.annotation_table.selectRow(0)
    assert window.geometry_combo.currentData() == "time_frequency_polygon"
    assert not window.geometry_combo.isEnabled()
    assert not window.update_annotation_button.isEnabled()
    assert not window.note_edit.isEnabled()
    assert "Polygon editing is unavailable" in window.editor_mode.text()
    drag(window.waveform, 300, 600, lane=True)
    assert window.controller.session.annotations == (polygon,)
    window._new_annotation()
    assert window.geometry_combo.findData("time_frequency_polygon") == -1
    window.close()
