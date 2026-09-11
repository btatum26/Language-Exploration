from uuid import uuid4

import pytest
from PySide6 import QtCore

from application import CreateRecordingCommand, RecoveryStorageError, SyncState
from gui.main_window import MainWindow
from tests.test_application_api import MemoryPersistenceStore
from tests.test_core_library import make_api
from tests.test_gui import DeferredRunner, ImmediateRunner, _write_wav


@pytest.fixture
def editor(qtbot, tmp_path):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    source = tmp_path / "clip.wav"
    _write_wav(source)
    created = api.import_recording(CreateRecordingCommand(
        recording_id=uuid4(), source_audio_path=source, name="Original", language="en"
    ))
    created.close()
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window, before_close_func=lambda widget: widget.shutdown())
    window.show()
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    yield window, api, store
    window.shutdown()


def test_exit_waits_for_database_save_and_preserves_details(editor, qtbot):
    window, api, store = editor
    session = window.controller.session
    session.set_name("Changed")
    window.author_edit.setText("Editor")
    window.save_message_edit.setText("End of session")
    runner = DeferredRunner()
    window.controller._task_runner = runner
    QtCore.QTimer.singleShot(0, window.save_dialog.accept)
    assert not window.close()
    assert window.isVisible() and not session.closed
    assert store.load_snapshot(session.recording_id).name == "Original"
    # A second close while the save is running must not enqueue a duplicate.
    assert not window.close()
    assert len(runner.tasks) == 1
    runner.complete(0)
    assert window.isVisible() and not session.closed
    runner.complete(0)  # catalog refresh
    qtbot.waitUntil(lambda: window._shutdown)
    snapshot = store.load_snapshot(session.recording_id)
    assert snapshot.name == "Changed"
    assert snapshot.revision.author == "Editor"
    assert snapshot.revision.message == "End of session"
    assert session.closed and not window.isVisible()
    reopened = api.open_recording(session.recording_id)
    assert reopened.name == "Changed"
    reopened.close()


def test_cancel_exit_keeps_unsaved_session(editor):
    window, _, store = editor
    session = window.controller.session
    session.set_name("Changed")
    QtCore.QTimer.singleShot(0, window.save_dialog.reject)
    assert not window.close()
    assert window.isVisible() and session.dirty and not session.closed
    assert store.load_snapshot(session.recording_id).name == "Original"


def test_offline_exit_keeps_window_and_recovery_retry_allows_exit(editor, qtbot):
    window, api, store = editor
    session = window.controller.session
    session.set_name("Offline changes")
    store.offline = True
    QtCore.QTimer.singleShot(0, window.save_dialog.accept)
    assert not window.close()
    assert window.isVisible() and not window._exit_requested
    assert session.sync_state is SyncState.PENDING
    assert len(api.recovery.list_pending()) == 1
    assert not window.close()  # retry still offline
    assert window.isVisible() and not window._exit_requested
    store.offline = False
    window.retry_saves_action.trigger()
    assert not api.recovery.list_pending()
    assert window.controller.session.sync_state is SyncState.SYNCED
    assert window.close()
    assert store.load_snapshot(session.recording_id).name == "Offline changes"


def test_retry_recovers_recording_blocked_after_restart(editor):
    window, api, store = editor
    session = window.controller.session
    session.set_name("Recovered")
    store.offline = True
    window.controller.save()
    window.controller.close_session()
    store.offline = False
    window.controller.refresh_recordings()
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    assert window.controller.session is None
    assert "recovery operation" in window.error_banner.text()
    assert window.error_retry_button.isVisible()
    window.error_retry_button.click()
    assert not window.error_panel.isVisible()
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    assert window.controller.session.name == "Recovered"
    assert not api.recovery.list_pending()


def test_save_exception_does_not_exit(editor, monkeypatch):
    window, _, _ = editor
    session = window.controller.session
    session.set_name("Changed")

    def fail(**kwargs):
        raise RecoveryStorageError("Recovery disk unavailable")

    monkeypatch.setattr(session, "save", fail)
    QtCore.QTimer.singleShot(0, window.save_dialog.accept)
    assert not window.close()
    assert window.isVisible() and session.dirty and not window._exit_requested
    assert "Recovery disk unavailable" in window.error_banner.text()


def test_conflict_on_exit_keeps_recovery_and_does_not_overwrite(editor):
    window, api, store = editor
    session = window.controller.session
    other = api.open_recording(session.recording_id)
    other.set_name("Other editor")
    other.save()
    other.close()
    session.set_name("Local changes")
    QtCore.QTimer.singleShot(0, window.save_dialog.accept)
    assert not window.close()
    assert window.isVisible() and not window._exit_requested
    assert session.sync_state is SyncState.CONFLICT
    assert len(api.recovery.list_conflicts()) == 1
    assert not window.close()
    assert store.load_snapshot(session.recording_id).name == "Other editor"


def test_close_during_existing_operation_continues_to_save(editor, qtbot):
    window, _, store = editor
    session = window.controller.session
    session.set_name("Changed while open")
    runner = DeferredRunner()
    window.controller._task_runner = runner
    window.controller.refresh_recordings()
    assert not window.close()
    runner.complete(0)
    QtCore.QTimer.singleShot(0, window.save_dialog.accept)
    qtbot.waitUntil(lambda: bool(runner.tasks))
    runner.complete(0)
    runner.complete(0)
    qtbot.waitUntil(lambda: window._shutdown)
    assert store.load_snapshot(session.recording_id).name == "Changed while open"
