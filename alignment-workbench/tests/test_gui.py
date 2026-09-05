from __future__ import annotations

import wave
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

import pytest
from PySide6 import QtWidgets

from application import (
    AnnotationLibraryListItem,
    AudioAvailability,
    CreateRecordingCommand,
    RecordingListItem,
    ResolvedAudio,
    Saved,
    SyncState,
)
from gui.app import run_started_gui
from gui.controller import WorkbenchController
from gui.main_window import MainWindow
from models import (
    AnnotatedRecordingSnapshot,
    AudioAsset,
    ConceptRef,
    Geometry,
    Library,
    LibraryEntry,
    LibraryVersion,
    PinnedLibraryVersion,
    RevisionMetadata,
    SignalAnnotation,
)

T = TypeVar("T")
_HASH = "1" * 64


class ImmediateRunner:
    def submit(
        self,
        operation: Callable[[], T],
        on_success: Callable[[T], None],
        on_failure: Callable[[BaseException], None],
    ) -> None:
        try:
            result = operation()
        except BaseException as exc:
            on_failure(exc)
        else:
            on_success(result)


class DeferredRunner:
    def __init__(self) -> None:
        self.tasks: list[
            tuple[
                Callable[[], Any],
                Callable[[Any], None],
                Callable[[BaseException], None],
            ]
        ] = []

    def submit(
        self,
        operation: Callable[[], T],
        on_success: Callable[[T], None],
        on_failure: Callable[[BaseException], None],
    ) -> None:
        self.tasks.append((operation, on_success, on_failure))

    def complete(self, index: int) -> None:
        operation, success, failure = self.tasks.pop(index)
        try:
            result = operation()
        except BaseException as exc:
            failure(exc)
        else:
            success(result)


class FakeLibraries:
    def __init__(self, library: Library, version: LibraryVersion) -> None:
        self.library = library
        self.version = version
        self.version_calls = 0

    def get(self, library_id: UUID) -> Library:
        assert library_id == self.library.id
        return self.library

    def get_version(self, library_version_id: UUID) -> LibraryVersion:
        assert library_version_id == self.version.id
        self.version_calls += 1
        return self.version


class FakeSession:
    def __init__(
        self,
        *,
        recording_id: UUID,
        audio: ResolvedAudio,
        version: LibraryVersion,
        base_revision_id: UUID,
        name: str,
    ) -> None:
        self._recording_id = recording_id
        self._audio = audio
        self._version = version
        self._base_revision_id = base_revision_id
        self._name = name
        self._annotations: tuple[SignalAnnotation, ...] = ()
        self._pinned: tuple[LibraryVersion, ...] = ()
        self._undo: list[tuple[tuple[SignalAnnotation, ...], tuple[LibraryVersion, ...]]] = []
        self._redo: list[tuple[tuple[SignalAnnotation, ...], tuple[LibraryVersion, ...]]] = []
        self._dirty = False
        self._closed = False

    @property
    def recording_id(self) -> UUID:
        return self._recording_id

    @property
    def base_revision_id(self) -> UUID:
        return self._base_revision_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def language(self) -> str:
        return "en"

    @property
    def default_speaker_ref(self) -> UUID | None:
        return None

    @property
    def audio(self) -> ResolvedAudio:
        return self._audio

    @property
    def annotations(self) -> tuple[SignalAnnotation, ...]:
        return self._annotations

    @property
    def pinned_libraries(self) -> tuple[LibraryVersion, ...]:
        return self._pinned

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def sync_state(self) -> SyncState:
        return SyncState.SYNCED

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, *, discard_unsaved_changes: bool = False) -> None:
        if self._dirty and not discard_unsaved_changes:
            raise RuntimeError("unsaved changes")
        self._closed = True

    def list_available_concepts(self) -> tuple[LibraryEntry, ...]:
        return self._version.entries if self._pinned else ()

    def create_annotation(
        self,
        *,
        concept_ref: ConceptRef,
        geometry: Geometry,
        attributes: Mapping[str, object] | None = None,
        confidence: float | None = None,
        note: str | None = None,
        provenance_ref: str | None = None,
    ) -> SignalAnnotation:
        annotation = SignalAnnotation(
            id=uuid4(),
            concept_ref=concept_ref,
            geometry=geometry,
            attributes=dict(attributes or {}),
            confidence=confidence,
            note=note,
            provenance_ref=provenance_ref,
        )
        self._push()
        self._annotations += (annotation,)
        return annotation

    def get_annotation(self, annotation_id: UUID) -> SignalAnnotation:
        return next(item for item in self._annotations if item.id == annotation_id)

    def replace_annotation(
        self,
        annotation_id: UUID,
        replacement: SignalAnnotation,
    ) -> SignalAnnotation:
        self._push()
        self._annotations = tuple(
            replacement if item.id == annotation_id else item for item in self._annotations
        )
        return replacement

    def remove_annotation(self, annotation_id: UUID) -> SignalAnnotation:
        removed = self.get_annotation(annotation_id)
        self._push()
        self._annotations = tuple(item for item in self._annotations if item.id != annotation_id)
        return removed

    def pin_library_version(self, version: LibraryVersion) -> None:
        if version in self._pinned:
            return
        self._push()
        self._pinned += (version,)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append((self._annotations, self._pinned))
        self._annotations, self._pinned = self._undo.pop()
        self._dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append((self._annotations, self._pinned))
        self._annotations, self._pinned = self._redo.pop()
        self._dirty = True
        return True

    def save(self, *, author: str | None = None, message: str | None = None) -> Saved:
        revision = RevisionMetadata(
            id=uuid4(),
            number=2,
            parent_id=self._base_revision_id,
            created_at=datetime.now(UTC),
            author=author,
            message=message,
        )
        self._base_revision_id = revision.id
        self._dirty = False
        return Saved(
            AnnotatedRecordingSnapshot(
                recording_id=self.recording_id,
                revision=revision,
                name=self.name,
                language=self.language,
                audio_asset=self.audio.asset,
                libraries=tuple(
                    PinnedLibraryVersion(
                        namespace="test",
                        version=version.version_label,
                        content_sha256=version.content_sha256,
                    )
                    for version in self._pinned
                ),
                annotations=self.annotations,
            )
        )

    def _push(self) -> None:
        self._undo.append((self._annotations, self._pinned))
        self._redo.clear()
        self._dirty = True


class FakeAPI:
    def __init__(
        self,
        *,
        item: RecordingListItem,
        library_item: AnnotationLibraryListItem,
        library: Library,
        version: LibraryVersion,
        sessions: dict[UUID, FakeSession],
    ) -> None:
        self.item = item
        self.library_item = library_item
        self.libraries = FakeLibraries(library, version)
        self.sessions = sessions
        self.open_calls: list[UUID] = []
        self.import_commands: list[CreateRecordingCommand] = []

    def list_recordings(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RecordingListItem, ...]:
        del limit, offset
        return (self.item,)

    def list_annotation_libraries(self) -> tuple[AnnotationLibraryListItem, ...]:
        return (self.library_item,)

    def import_recording(self, command: CreateRecordingCommand) -> FakeSession:
        self.import_commands.append(command)
        return next(iter(self.sessions.values()))

    def open_recording(self, recording_id: UUID) -> FakeSession:
        self.open_calls.append(recording_id)
        return self.sessions[recording_id]


@pytest.fixture
def gui_objects(tmp_path: Path) -> tuple[FakeAPI, FakeSession]:
    audio_path = tmp_path / "short.wav"
    _write_wav(audio_path)
    recording_id = uuid4()
    revision_id = uuid4()
    audio_asset = AudioAsset(
        id=uuid4(),
        sha256=_HASH,
        storage_uri=f"registry-audio://assets/{uuid4()}",
        sample_rate_hz=8_000,
        frame_count=800,
        channels=1,
    )
    library = Library(id=uuid4(), namespace="test", name="Test concepts")
    version_id = uuid4()
    version = LibraryVersion(
        id=version_id,
        library_id=library.id,
        version_label="1.0",
        content_sha256=_HASH,
        created_at=datetime.now(UTC),
        entries=(
            LibraryEntry(
                id=uuid4(),
                library_version_id=version_id,
                entry_key="speech",
                display_name="Speech",
                description="A speech interval",
                allowed_geometry_types=("time_interval",),
            ),
        ),
    )
    item = RecordingListItem(
        recording_id=recording_id,
        display_name="Reference clip",
        speaker_display_name="Speaker A",
        duration_seconds=0.1,
        current_revision_id=revision_id,
        revision_number=1,
        modified_at=datetime.now(UTC),
        audio_status=AudioAvailability.AVAILABLE,
    )
    library_item = AnnotationLibraryListItem(
        library_id=library.id,
        namespace=library.namespace,
        name=library.name,
        description=None,
        latest_version_id=version.id,
        latest_version_label=version.version_label,
        latest_version_created_at=version.created_at,
        entry_count=1,
    )
    session = FakeSession(
        recording_id=recording_id,
        audio=ResolvedAudio(audio_asset, audio_path),
        version=version,
        base_revision_id=revision_id,
        name=item.display_name,
    )
    return (
        FakeAPI(
            item=item,
            library_item=library_item,
            library=library,
            version=version,
            sessions={recording_id: session},
        ),
        session,
    )


def test_main_window_walks_annotation_edit_undo_redo_and_save(
    qtbot: Any,
    gui_objects: tuple[FakeAPI, FakeSession],
) -> None:
    api, session = gui_objects
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.show()

    assert window.recordings_list.count() == 1
    assert window.libraries_list.count() == 1
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    assert window.controller.session is session
    assert window.name_value.text() == "Reference clip"
    assert "Speaker A" in window.speaker_value.text()

    window.libraries_list.setCurrentRow(0)
    window.pin_library_button.click()
    assert window.concept_combo.count() == 1
    assert session.dirty

    window.start_sample.setValue(100)
    window.end_sample.setValue(250)
    window.note_edit.setText("initial")
    window.create_annotation_button.click()
    assert window.annotation_table.rowCount() == 1
    annotation_id = session.annotations[0].id

    window.annotation_table.selectRow(0)
    assert str(annotation_id) in window.annotation_details.toPlainText()
    window.end_sample.setValue(300)
    window.note_edit.setText("updated")
    window.update_annotation_button.click()
    assert session.annotations[0].geometry.end_sample == 300
    assert session.annotations[0].note == "updated"

    window.undo_action.trigger()
    assert session.annotations[0].geometry.end_sample == 250
    window.redo_action.trigger()
    assert session.annotations[0].geometry.end_sample == 300

    window.save_message_edit.setText("GUI save")
    window.save_action.trigger()
    assert not session.dirty
    assert "r2" in window.revision_value.text()
    assert window.session_state_value.text() == "clean · synced"
    window.shutdown()


def test_dirty_recording_requires_explicit_discard(
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    gui_objects: tuple[FakeAPI, FakeSession],
) -> None:
    api, session = gui_objects
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    window.recordings_list.setCurrentRow(0)
    window.open_action.trigger()
    window.libraries_list.setCurrentRow(0)
    window.pin_library_button.click()
    assert session.dirty

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Cancel,
    )
    assert not window._prepare_to_replace_session()
    assert window.controller.session is session

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Discard,
    )
    assert window._prepare_to_replace_session()
    assert window.controller.session is None
    assert session.closed
    window.shutdown()


def test_import_action_uses_native_picker_values_and_opens_result(
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    gui_objects: tuple[FakeAPI, FakeSession],
) -> None:
    api, session = gui_objects
    window = MainWindow(api, task_runner=ImmediateRunner())
    qtbot.addWidget(window)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(session.audio.local_path), "PCM WAV audio (*.wav)"),
    )
    responses = iter((("Imported name", True), ("it", True)))
    monkeypatch.setattr(
        QtWidgets.QInputDialog,
        "getText",
        lambda *args, **kwargs: next(responses),
    )

    window.import_action.trigger()

    assert len(api.import_commands) == 1
    command = api.import_commands[0]
    assert command.source_audio_path == session.audio.local_path
    assert command.name == "Imported name"
    assert command.language == "it"
    assert window.controller.session is session
    window.shutdown()


def test_stale_open_result_cannot_replace_newer_recording(
    qtbot: Any,
    gui_objects: tuple[FakeAPI, FakeSession],
) -> None:
    del qtbot
    api, first_session = gui_objects
    second_id = uuid4()
    second_item = RecordingListItem(
        recording_id=second_id,
        display_name="Second",
        speaker_display_name=None,
        duration_seconds=0.1,
        current_revision_id=uuid4(),
        revision_number=1,
        modified_at=datetime.now(UTC),
        audio_status=AudioAvailability.AVAILABLE,
    )
    second_session = FakeSession(
        recording_id=second_id,
        audio=first_session.audio,
        version=first_session._version,
        base_revision_id=second_item.current_revision_id,
        name="Second",
    )
    api.sessions[second_id] = second_session
    runner = DeferredRunner()
    controller = WorkbenchController(api, runner)

    controller.open_recording(api.item)
    controller.open_recording(second_item)
    runner.complete(1)
    assert controller.session is second_session
    runner.complete(0)
    assert controller.session is second_session
    assert first_session.closed


def test_lifecycle_starts_before_window_and_always_shuts_down(qapp: Any) -> None:
    events: list[str] = []

    class Lifecycle:
        api = object()

        def start(self) -> Lifecycle:
            events.append("start")
            return self

        def shutdown(self) -> None:
            events.append("application-shutdown")

    class Window:
        def __init__(self, _api: object) -> None:
            events.append("window-created")

        def show(self) -> None:
            events.append("window-shown")

        def shutdown(self) -> None:
            events.append("window-shutdown")

    result = run_started_gui(
        qapp,
        Lifecycle(),
        window_factory=Window,
        event_loop=lambda: 17,
    )

    assert result == 17
    assert events == [
        "start",
        "window-created",
        "window-shown",
        "window-shutdown",
        "application-shutdown",
    ]


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8_000)
        target.writeframes(b"\x00\x00" * 800)
