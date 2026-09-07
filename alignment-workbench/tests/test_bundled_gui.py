from PySide6 import QtCore

from gui.main_window import MainWindow
from gui.waveform import annotation_label
from models import ConceptRef
from tests.test_application_api import MemoryPersistenceStore
from tests.test_core_library import make_api
from tests.test_first_use_gui import click_editor, discard_on_test_teardown, drag  # noqa: F401
from tests.test_gui import ImmediateRunner, _write_wav


def test_bundled_concepts_filters_labels_and_markers(qtbot, tmp_path):
    api = make_api(MemoryPersistenceStore(), tmp_path)
    versions = api.ensure_bundled_libraries()
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.resize(1024, 768)
    window.show()
    source = tmp_path / "sounds.wav"
    _write_wav(source)
    window.controller.import_recording(source, name="Sounds", language="und")
    qtbot.waitUntil(lambda: window.waveform.audio_ready)
    session = window.controller.session
    assert session.pinned_libraries == (versions[0],)
    assert window.libraries_list.count() == 3
    for item in window.controller.libraries:
        if item.namespace != "core":
            window.controller.pin_latest_library(item)
    assert session.pinned_libraries == versions
    window.undo_action.trigger()
    assert len(session.pinned_libraries) == 2
    window.redo_action.trigger()
    assert session.pinned_libraries == versions
    window.concept_search.setText("ɛ")
    assert window.concept_combo.count() == 1
    assert "ɛ" in window.concept_combo.currentText()
    assert window._current_concept_reference() == ConceptRef("phonetics@0.1:epsilon")
    window.category_filter.setCurrentIndex(window.category_filter.findData("consonant"))
    assert window.concept_combo.count() == 0
    window.category_filter.setCurrentIndex(0)
    drag(window.waveform, 100, 600)
    click_editor(qtbot, window, window.create_annotation_button)
    vowel = session.annotations[0]
    assert vowel.label is None
    assert window.annotation_table.item(0, 6).text() == "ɛ"
    assert annotation_label(vowel, window.waveform.definitions) == "ɛ"
    window._new_annotation()
    window.concept_search.setText("pitch-rise")
    click_editor(qtbot, window, window.create_annotation_button)
    assert window.annotation_table.item(1, 6).text() == "Pitch rise"
    window._new_annotation()
    window.concept_search.setText("marker")
    window.waveform.clear_selection()
    assert window.geometry_combo.currentData() == "point"
    assert window.create_annotation_button.isEnabled()
    assert not window.end_sample.isEnabled()
    window._seek_sample(350)
    assert window.start_sample.value() == 350
    window.label_edit.setText("Cue")
    click_editor(qtbot, window, window.create_annotation_button)
    marker = session.annotations[-1]
    assert marker.geometry.start_sample == 350 and marker.geometry.end_sample is None
    assert window.annotation_table.item(2, 6).text() == "Cue"
    window.undo_action.trigger()
    assert marker not in session.annotations
    window.redo_action.trigger()
    assert marker in session.annotations
    window._new_annotation()
    window.waveform.grab()
    rect = window.waveform._annotation_rects[marker.id]
    assert rect.width() >= 8
    qtbot.mouseClick(window.waveform, QtCore.Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert window._selected_annotation_id == marker.id
    # Selecting an annotation hidden by the current filter restores its exact concept.
    window._new_annotation()
    window.concept_search.setText("silence")
    window.annotation_table.selectRow(0)
    assert window._current_concept_reference() == vowel.concept_ref
    assert window.label_edit.text() == ""
    assert annotation_label(vowel, {}) == str(vowel.concept_ref)
    session.save()
    recording_id = session.recording_id
    expected = session.annotations
    window.controller.close_session()
    reopened = api.open_recording(recording_id)
    assert reopened.annotations == expected and reopened.pinned_libraries == versions
    reopened.close()
    window.close()
