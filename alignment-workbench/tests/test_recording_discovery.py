"""Focused discovery behavior and immutable metadata revision coverage."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from PySide6 import QtCore, QtWidgets

from application import CreateRecordingCommand, RecordingListItem, Saved
from application.read_models import AudioAvailability
from gui.controller import WorkbenchController
from gui.metadata_editor import MetadataEditor
from gui.recording_discovery import filtered, grouped, selected_once, values
from gui.recordings_browser import RecordingsBrowser
from models import RecordingMetadata
from tests.test_application_api import MemoryPersistenceStore, write_wav
from tests.test_core_library import make_api
from tests.test_gui import ImmediateRunner


def item(name="Same title", **metadata):
    return RecordingListItem(
        uuid4(),
        name,
        None,
        2.5,
        uuid4(),
        1,
        datetime.now(UTC),
        AudioAvailability.AVAILABLE,
        metadata=RecordingMetadata(**metadata),
    )


def test_combined_filters_search_sort_and_unspecified():
    a = item(speakers=(" Ben ", "LUCA"), languages=("IT",), tags=("vowel", "story"))
    b = item(speakers=("Miri",), languages=("en",), tags=("story",))
    c = item()
    assert a.metadata.speakers == ("ben", "luca")
    assert filtered([a, b, c], "", {"speakers": {"ben", "miri"}, "languages": {"it"}}, "Name") == [
        a
    ]
    assert filtered([a, b], "BEN", {"tags": {"story"}}, "Name") == [a]
    assert filtered([a, b], "vowel", {}, "Name") == [a]
    transcript = replace(b, transcript="I'm full")
    assert filtered([a, transcript], "full", {}, "Name") == [transcript]
    assert values(c, "languages") == ("Unspecified",)
    assert grouped([a, b, c], "speakers")["luca"] == [a]
    assert (
        filtered([a, replace(b, duration_seconds=1)], "", {}, "Duration")[0].recording_id
        == b.recording_id
    )
    assert selected_once([a, a, b], {a.recording_id}) == [a]


@pytest.fixture
def browser(qtbot, tmp_path, monkeypatch):
    # Keep browser preferences isolated from the actual user's remembered view.
    settings = QtCore.QSettings(str(tmp_path / "browser.ini"), QtCore.QSettings.Format.IniFormat)
    monkeypatch.setattr("gui.recordings_browser.QtCore.QSettings", lambda *args: settings)
    api = make_api(MemoryPersistenceStore(), tmp_path)
    parent = QtWidgets.QWidget()
    qtbot.addWidget(parent)
    catalog = WorkbenchController(api, ImmediateRunner())
    dialog = RecordingsBrowser(catalog, parent, ImmediateRunner())
    qtbot.addWidget(dialog)
    yield dialog
    dialog.close()
    catalog.shutdown()


def groups(browser):
    return {
        row.data(0, QtCore.Qt.ItemDataRole.UserRole): row
        for row in (browser.items.topLevelItem(i) for i in range(browser.items.topLevelItemCount()))
    }


def test_independent_expansion_selection_duplicates_and_add_once(browser):
    a = item(speakers=("Ben", "Luca"))
    b = item(speakers=("Miri",))
    browser.populate((a, b))
    browser.group_by.setCurrentIndex(browser.group_by.findData("speakers"))
    groups(browser)["ben"].child(0).setCheckState(0, QtCore.Qt.CheckState.Checked)
    assert browser.selected == {a.recording_id}
    assert groups(browser)["luca"].child(0).checkState(0) == QtCore.Qt.CheckState.Checked
    groups(browser)["ben"].setExpanded(False)
    assert groups(browser)["luca"].isExpanded()
    assert groups(browser)["miri"].isExpanded()
    assert "1 selected" in groups(browser)["ben"].text(0)
    browser.search.setText("Miri")
    assert browser.selected == {a.recording_id}
    assert "1 hidden" in browser.selection_state.text()
    browser.search.clear()
    assert not groups(browser)["ben"].isExpanded()
    assert groups(browser)["luca"].isExpanded()
    browser.group_by.setCurrentIndex(browser.group_by.findData("languages"))
    browser._expand_all(False)
    browser.group_by.setCurrentIndex(browser.group_by.findData("speakers"))
    assert not groups(browser)["ben"].isExpanded()
    assert groups(browser)["luca"].isExpanded()
    browser.populate((a, b))
    assert browser.selected == {a.recording_id}
    assert not groups(browser)["ben"].isExpanded()
    received = []
    browser.add_requested.connect(received.append)
    browser._add()
    assert received == [a]


def test_bulk_editor_preserves_untouched_fields_and_clears_explicitly(qtbot):
    original = RecordingMetadata(speakers=("Ben",), tags=("old",), languages=("it",))
    editor = MetadataEditor(metadata=original, bulk=True)
    qtbot.addWidget(editor)
    editor.enabled_fields["tags"].setChecked(True)
    editor.fields["tags"].clear()
    changed = editor.metadata(original)
    assert changed.speakers == ("ben",) and changed.languages == ("it",)
    assert changed.tags == ()
    cleared = replace(
        item(),
        language="it",
        speaker_display_name="Ben",
        metadata=RecordingMetadata(legacy_labels=False),
    )
    assert values(cleared, "languages") == values(cleared, "speakers") == ("Unspecified",)


def test_metadata_import_revision_undo_reopen_and_annotation_save(tmp_path):
    store = MemoryPersistenceStore()
    api = make_api(store, tmp_path)
    source = tmp_path / "sample.wav"
    write_wav(source)
    metadata = RecordingMetadata(
        languages=(" IT ", "it"),
        speakers=("Ben", "Luca"),
        collections=("Session 1",),
        varieties=("it: roman",),
        tags=("story",),
    )
    session = api.import_recording(
        CreateRecordingCommand(
            recording_id=uuid4(),
            source_audio_path=source,
            name="Sample",
            language="und",
            metadata=metadata,
        )
    )
    from models import TimeIntervalGeometry

    session.pin_library_version(api.ensure_core_library())
    session.create_annotation(
        concept_ref="core@0.1:word",
        geometry=TimeIntervalGeometry(start_sample=0, end_sample=10),
        label="Original words",
    )
    session.save()
    first = store.load_snapshot(session.recording_id)
    assert first.metadata == metadata
    changed = RecordingMetadata(speakers=("Miri",), tags=("new",))
    session.set_metadata(changed)
    assert session.undo() and session.metadata == metadata
    assert session.redo() and session.metadata == changed
    result = session.save()
    assert isinstance(result, Saved)
    assert result.snapshot.metadata == changed
    assert result.snapshot.audio_asset == first.audio_asset
    assert result.snapshot.annotations == first.annotations
    assert store.load_snapshot(session.recording_id, first.revision.id) == first
    session.close()
    reopened = api.open_recording(first.recording_id)
    reopened.set_name("Changed title")
    assert reopened.save().snapshot.metadata == changed
    reopened.close()


def test_settings_keyboard_and_multiselect_are_preserved(browser, qtbot):
    a, b = item(speakers=("Ben",)), item(speakers=("Luca",))
    browser.populate((a, b))
    browser.group_by.setCurrentIndex(browser.group_by.findData("speakers"))
    browser.show()
    row = groups(browser)["ben"]
    browser.items.setCurrentItem(row)
    browser.items.setFocus()
    qtbot.keyClick(browser.items, QtCore.Qt.Key.Key_Left)
    assert not row.isExpanded()
    assert groups(browser)["luca"].isExpanded()
    browser._expand_all(True)
    first = groups(browser)["ben"].child(0)
    second = groups(browser)["luca"].child(0)
    first.setSelected(True)
    second.setSelected(True)
    first.setCheckState(0, QtCore.Qt.CheckState.Checked)
    assert browser.selected == {a.recording_id, b.recording_id}
    browser._toggle("speakers", "ben", True)
    assert "1 hidden" in browser.selection_state.text()
    browser.sort.setCurrentText("Name")
    clone = RecordingsBrowser(browser.catalog, browser.parentWidget(), ImmediateRunner())
    qtbot.addWidget(clone)
    assert clone.filters["speakers"] == {"ben"}
    assert clone.sort.currentText() == "Name"
    assert clone.expansion == browser.expansion
    browser._clear_selection()
    assert not browser.selected


def test_preview_uses_worker_without_add_and_late_completion_is_ignored(browser, tmp_path):
    source = tmp_path / "preview.wav"
    write_wav(source)
    api = browser.catalog.api
    session = api.import_recording(
        CreateRecordingCommand(
            recording_id=uuid4(), source_audio_path=source, name="Preview", language="und"
        )
    )
    session.close()
    browser.populate(api.list_recordings())
    browser.show()
    browser.items.setCurrentItem(browser.items.topLevelItem(0))
    pending = []

    class Deferred:
        def submit(self, operation, success, failure):
            pending.append((operation, success, failure))

    browser.runner = Deferred()
    added = []
    browser.add_requested.connect(added.append)
    browser._preview()
    assert browser.state.text().startswith("Loading preview")
    assert not added and len(pending) == 1
    browser.reject()
    operation, success, _ = pending.pop()
    success(operation())
    assert browser.player.source().isEmpty()


def test_bulk_save_updates_each_selected_recording_once(browser, tmp_path, monkeypatch):
    source = tmp_path / "bulk.wav"
    write_wav(source)
    api = browser.catalog.api
    for title in ("A", "B"):
        session = api.import_recording(
            CreateRecordingCommand(
                recording_id=uuid4(),
                source_audio_path=source,
                name=title,
                language="it",
                metadata=RecordingMetadata(speakers=("Ben", "Luca")),
            )
        )
        session.close()
    recordings = api.list_recordings()
    browser.populate(recordings)
    browser.selected = {item.recording_id for item in recordings}

    def accept(dialog):
        editor = dialog.findChild(MetadataEditor)
        editor.enabled_fields["tags"].setChecked(True)
        editor.fields["tags"].setText("Updated, UPDATED")
        return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec", accept)
    browser._edit()
    assert not browser._saving
    for recording in api.list_recordings():
        assert recording.revision_number == 2
        assert recording.metadata.tags == ("updated",)
        assert recording.metadata.languages == ("it",)
        assert recording.metadata.speakers == ("ben", "luca")
