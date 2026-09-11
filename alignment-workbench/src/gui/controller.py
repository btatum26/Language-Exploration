"""GUI orchestration over only WorkbenchAPI and RecordingEditSession."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, TypeVar
from uuid import UUID, uuid4

from pydantic import ValidationError
from PySide6 import QtCore

from application import (
    AnnotationLibraryListItem,
    CreateRecordingCommand,
    PendingRecoveryOperationError,
    PendingSave,
    Queued,
    RecordingListItem,
    RecoveryConflict,
    RecoveryResult,
    ResolvedAudio,
    SaveConflict,
    Saved,
    SaveResult,
    SyncState,
    WorkbenchError,
)
from gui.tasks import TaskSubmitter
from models import (
    ConceptRef,
    Geometry,
    Library,
    LibraryEntry,
    LibraryVersion,
    PinnedLibraryVersion,
    PointGeometry,
    SignalAnnotation,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
    TimeIntervalGeometry,
)

T = TypeVar("T")


class EditableRecording(Protocol):
    @property
    def recording_id(self) -> UUID: ...

    @property
    def base_revision_id(self) -> UUID: ...

    @property
    def name(self) -> str: ...

    @property
    def language(self) -> str: ...

    @property
    def default_speaker_ref(self) -> UUID | None: ...

    @property
    def audio(self) -> ResolvedAudio: ...

    @property
    def annotations(self) -> tuple[SignalAnnotation, ...]: ...

    @property
    def pinned_libraries(self) -> tuple[LibraryVersion, ...]: ...

    @property
    def dirty(self) -> bool: ...

    @property
    def sync_state(self) -> SyncState: ...

    @property
    def can_undo(self) -> bool: ...

    @property
    def can_redo(self) -> bool: ...

    @property
    def closed(self) -> bool: ...

    def close(self, *, discard_unsaved_changes: bool = False) -> None: ...

    def list_available_concepts(self) -> tuple[LibraryEntry, ...]: ...

    def create_annotation(
        self,
        *,
        concept_ref: ConceptRef,
        geometry: Geometry,
        attributes: Mapping[str, object] | None = None,
        confidence: float | None = None,
        label: str | None = None,
        note: str | None = None,
        provenance_ref: str | None = None,
    ) -> SignalAnnotation: ...

    def get_annotation(self, annotation_id: UUID) -> SignalAnnotation: ...

    def replace_annotation(
        self,
        annotation_id: UUID,
        replacement: SignalAnnotation,
    ) -> SignalAnnotation: ...

    def remove_annotation(self, annotation_id: UUID) -> SignalAnnotation: ...

    def pin_library_version(self, version: LibraryVersion) -> None: ...

    def undo(self) -> bool: ...

    def redo(self) -> bool: ...

    def save(self, *, author: str | None = None, message: str | None = None) -> SaveResult: ...


class LibraryClient(Protocol):
    def get(self, library_id: UUID) -> Library: ...

    def get_version(self, library_version_id: UUID) -> LibraryVersion: ...


class ApplicationClient(Protocol):
    @property
    def recovery(self) -> RecoveryClient: ...

    @property
    def libraries(self) -> LibraryClient: ...

    def ensure_core_library(self) -> LibraryVersion: ...

    def list_recordings(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RecordingListItem, ...]: ...

    def list_annotation_libraries(self) -> tuple[AnnotationLibraryListItem, ...]: ...

    def import_recording(self, command: CreateRecordingCommand) -> EditableRecording: ...

    def open_recording(self, recording_id: UUID) -> EditableRecording: ...


class RecoveryClient(Protocol):
    def retry_all(self) -> tuple[RecoveryResult, ...]: ...

    def list_pending(self) -> tuple[PendingSave, ...]: ...

    def list_conflicts(self) -> tuple[RecoveryConflict, ...]: ...


@dataclass(frozen=True, slots=True)
class ConceptChoice:
    reference: ConceptRef
    entry: LibraryEntry

    @property
    def display_text(self) -> str:
        symbol = self.entry.metadata.get("ipa_symbol", "")
        return f"{symbol} {self.entry.display_name} — {self.reference}".strip()


@dataclass(frozen=True, slots=True)
class _OpenedRecording:
    session: EditableRecording
    catalog_item: RecordingListItem
    namespaces_by_version_id: Mapping[UUID, str]


@dataclass(frozen=True, slots=True)
class _ImportedRecording:
    opened: _OpenedRecording
    recordings: tuple[RecordingListItem, ...]


class WorkbenchController(QtCore.QObject):
    """Own transient GUI navigation state while the edit session owns edits."""

    recordings_loading = QtCore.Signal()
    recordings_changed = QtCore.Signal(object)
    recordings_failed = QtCore.Signal(str)
    libraries_loading = QtCore.Signal()
    libraries_changed = QtCore.Signal(object)
    libraries_failed = QtCore.Signal(str)
    annotation_created = QtCore.Signal(object)
    session_changed = QtCore.Signal(object)
    busy_changed = QtCore.Signal(bool)
    error = QtCore.Signal(str)
    recovery_error = QtCore.Signal(str)
    notice = QtCore.Signal(str)
    save_finished = QtCore.Signal(bool)
    recovery_finished = QtCore.Signal(bool)

    def __init__(
        self,
        api: ApplicationClient,
        task_runner: TaskSubmitter,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._task_runner = task_runner
        self._recordings: tuple[RecordingListItem, ...] = ()
        self._libraries: tuple[AnnotationLibraryListItem, ...] = ()
        self._session: EditableRecording | None = None
        self._catalog_item: RecordingListItem | None = None
        self._namespaces_by_version_id: Mapping[UUID, str] = MappingProxyType({})
        self._busy_count = 0
        self._generation = 0
        self._picker_versions: dict[str, LibraryVersion] = {}

    def load_picker_libraries(self) -> None:
        """Discover published definitions without pinning anything to the recording."""

        def operation() -> dict[str, LibraryVersion]:
            return {
                item.namespace: self._api.libraries.get_version(item.latest_version_id)
                for item in self._libraries
                if item.latest_version_id is not None
            }

        def success(versions: dict[str, LibraryVersion]) -> None:
            self._picker_versions = versions
            self.session_changed.emit(self._session)

        self._run(
            operation,
            success,
            lambda exc: self.error.emit(f"Could not load definitions: {_error_message(exc)}"),
        )

    @property
    def recordings(self) -> tuple[RecordingListItem, ...]:
        return self._recordings

    @property
    def libraries(self) -> tuple[AnnotationLibraryListItem, ...]:
        return self._libraries

    @property
    def session(self) -> EditableRecording | None:
        return self._session

    @property
    def catalog_item(self) -> RecordingListItem | None:
        return self._catalog_item

    @property
    def busy(self) -> bool:
        return self._busy_count > 0

    @property
    def available_concepts(self) -> tuple[ConceptChoice, ...]:
        session = self._session
        if session is None:
            return ()
        version_by_id = {version.id: version for version in session.pinned_libraries}
        choices: list[ConceptChoice] = []
        for entry in session.list_available_concepts():
            version = version_by_id.get(entry.library_version_id)
            namespace = self._namespaces_by_version_id.get(entry.library_version_id)
            if version is None or namespace is None:
                continue
            choices.append(
                ConceptChoice(
                    reference=ConceptRef(f"{namespace}@{version.version_label}:{entry.entry_key}"),
                    entry=entry,
                )
            )
        pinned_namespaces = {
            self._namespaces_by_version_id.get(version.id) for version in session.pinned_libraries
        }
        for namespace, version in self._picker_versions.items():
            if namespace in pinned_namespaces:
                continue
            for entry in version.entries:
                choices.append(
                    ConceptChoice(
                        ConceptRef(f"{namespace}@{version.version_label}:{entry.entry_key}"), entry
                    )
                )
        return tuple(choices)

    def refresh_all(self) -> None:
        self.refresh_recordings()
        self.refresh_libraries()

    def refresh_recordings(self) -> None:
        self.recordings_loading.emit()

        def success(items: tuple[RecordingListItem, ...]) -> None:
            self._recordings = items
            self.recordings_changed.emit(items)

        self._run(
            lambda: self._api.list_recordings(limit=500),
            success,
            lambda exc: self.recordings_failed.emit(_error_message(exc)),
        )

    def refresh_libraries(self) -> None:
        self.libraries_loading.emit()

        def success(items: tuple[AnnotationLibraryListItem, ...]) -> None:
            self._libraries = items
            self.libraries_changed.emit(items)

        self._run(
            self._api.list_annotation_libraries,
            success,
            lambda exc: self.libraries_failed.emit(_error_message(exc)),
        )

    def open_recording(self, item: RecordingListItem) -> None:
        if self._session is not None:
            self.error.emit("Close the active recording before opening another one")
            return
        generation = self._next_generation()

        def operation() -> _OpenedRecording:
            session = self._api.open_recording(item.recording_id)
            try:
                namespaces = self._resolve_namespaces(session)
                return _OpenedRecording(session, item, namespaces)
            except BaseException:
                session.close(discard_unsaved_changes=True)
                raise

        def failure(exc: BaseException) -> None:
            signal = (
                self.recovery_error
                if isinstance(exc, PendingRecoveryOperationError)
                else self.error
            )
            signal.emit(f"Could not open recording: {_error_message(exc)}")

        self._run(
            operation,
            lambda opened: self._install_if_current(opened, generation),
            failure,
        )

    def import_recording(self, source: Path, *, name: str, language: str) -> None:
        if self._session is not None:
            self.error.emit("Close the active recording before importing another one")
            return
        generation = self._next_generation()

        def operation() -> _ImportedRecording:
            core = self._api.ensure_core_library()
            session = self._api.import_recording(
                CreateRecordingCommand(
                    recording_id=uuid4(),
                    source_audio_path=source,
                    name=name,
                    language=language,
                    libraries=(
                        PinnedLibraryVersion(
                            namespace="core",
                            version="0.1",
                            content_sha256=core.content_sha256,
                        ),
                    ),
                )
            )
            try:
                recordings = self._api.list_recordings(limit=500)
                item = next(
                    candidate
                    for candidate in recordings
                    if candidate.recording_id == session.recording_id
                )
                opened = _OpenedRecording(session, item, self._resolve_namespaces(session))
                return _ImportedRecording(opened=opened, recordings=recordings)
            except BaseException:
                session.close(discard_unsaved_changes=True)
                raise

        def success(imported: _ImportedRecording) -> None:
            if generation != self._generation:
                imported.opened.session.close(discard_unsaved_changes=True)
                return
            self._recordings = imported.recordings
            self.recordings_changed.emit(imported.recordings)
            self._install(imported.opened)
            self.notice.emit(f"Imported {imported.opened.session.name}")

        self._run(
            operation,
            success,
            lambda exc: self.error.emit(f"Could not import recording: {_error_message(exc)}"),
        )

    def close_session(self, *, discard_unsaved_changes: bool = False) -> bool:
        session = self._session
        if session is None:
            return True
        try:
            session.close(discard_unsaved_changes=discard_unsaved_changes)
        except WorkbenchError as exc:
            self.error.emit(_error_message(exc))
            return False
        self._next_generation()
        self._session = None
        self._catalog_item = None
        self._namespaces_by_version_id = MappingProxyType({})
        self.session_changed.emit(None)
        return True

    def pin_latest_library(self, item: AnnotationLibraryListItem) -> None:
        if self.busy:
            return
        session = self._session
        version_id = item.latest_version_id
        if session is None:
            self.error.emit("Open a recording before pinning a library")
            return
        if version_id is None:
            self.error.emit(f"{item.name} has no published version")
            return
        generation = self._generation

        def success(version: LibraryVersion) -> None:
            if generation != self._generation or self._session is not session:
                return
            if not self._mutate(lambda: session.pin_library_version(version), internal=True):
                return
            namespaces = dict(self._namespaces_by_version_id)
            namespaces[version.id] = item.namespace
            self._namespaces_by_version_id = MappingProxyType(namespaces)
            self.session_changed.emit(session)
            self.notice.emit(f"Pinned {item.namespace}@{version.version_label}")

        self._run(
            lambda: self._api.libraries.get_version(version_id),
            success,
            lambda exc: self.error.emit(f"Could not load library: {_error_message(exc)}"),
        )

    @property
    def core_is_pinned(self) -> bool:
        return bool(
            self._session
            and any(
                self._namespaces_by_version_id.get(v.id) == "core" and v.version_label == "0.1"
                for v in self._session.pinned_libraries
            )
        )

    def use_core_library(self) -> None:
        session = self._require_session()
        if session is None or self.busy or self.core_is_pinned:
            return
        generation = self._generation

        def success(version: LibraryVersion) -> None:
            if generation != self._generation or self._session is not session:
                return
            namespaces = dict(self._namespaces_by_version_id)
            namespaces[version.id] = "core"
            self._namespaces_by_version_id = MappingProxyType(namespaces)
            self._mutate(lambda: session.pin_library_version(version), internal=True)

        self._run(
            self._api.ensure_core_library,
            success,
            lambda exc: self.error.emit(f"Could not use core library: {_error_message(exc)}"),
        )

    def move_boundary(self, annotation_id: UUID, start: int, end: int) -> None:
        session = self._require_session()
        if session is None or self.busy:
            return

        def operation() -> None:
            current = session.get_annotation(annotation_id)
            if not isinstance(current.geometry, TimeIntervalGeometry):
                raise ValueError("Boundary dragging supports time intervals only")
            replacement = current.model_copy(
                update={
                    "geometry": TimeIntervalGeometry(start_sample=start, end_sample=end),
                }
            )
            session.replace_annotation(annotation_id, replacement)

        if not self._mutate(operation):
            self.session_changed.emit(session)

    def create_annotation(
        self,
        *,
        concept_ref: ConceptRef,
        geometry_type: str,
        start_sample: int,
        end_sample: int,
        min_frequency_hz: float,
        max_frequency_hz: float,
        note: str | None,
        label: str | None = None,
    ) -> None:
        session = self._require_session()
        if session is None:
            return

        def operation() -> None:
            geometry = _new_geometry(
                geometry_type,
                start_sample=start_sample,
                end_sample=end_sample,
                min_frequency_hz=min_frequency_hz,
                max_frequency_hz=max_frequency_hz,
            )
            # Explicit creation is the point at which the selected publication is pinned.
            self._pin_concept_publication(session, concept_ref)
            annotation = session.create_annotation(
                concept_ref=concept_ref,
                geometry=geometry,
                label=label,
                note=note,
            )
            self.annotation_created.emit(annotation.id)

        self._mutate(operation)

    def _pin_concept_publication(self, session: EditableRecording, concept: ConceptRef) -> None:
        namespace = str(concept).split("@", 1)[0]
        version = self._picker_versions.get(namespace)
        if version is not None and not any(
            self._namespaces_by_version_id.get(pin.id) == namespace
            for pin in session.pinned_libraries
        ):
            session.pin_library_version(version)
            namespaces = dict(self._namespaces_by_version_id)
            namespaces[version.id] = namespace
            self._namespaces_by_version_id = MappingProxyType(namespaces)

    def update_annotation(
        self,
        annotation_id: UUID,
        *,
        concept_ref: ConceptRef,
        start_sample: int,
        end_sample: int,
        min_frequency_hz: float,
        max_frequency_hz: float,
        note: str | None,
        label: str | None = None,
    ) -> None:
        session = self._require_session()
        if session is None:
            return

        def operation() -> None:
            current = session.get_annotation(annotation_id)
            if isinstance(current.geometry, TimeFrequencyPolygonGeometry):
                raise ValueError("Polygon editing is not supported; the annotation is preserved")
            geometry = _replacement_geometry(
                current,
                start_sample=start_sample,
                end_sample=end_sample,
                min_frequency_hz=min_frequency_hz,
                max_frequency_hz=max_frequency_hz,
            )
            replacement = SignalAnnotation(
                id=current.id,
                concept_ref=concept_ref,
                geometry=geometry,
                attributes=dict(current.attributes),
                confidence=current.confidence,
                label=label,
                note=note,
                provenance_ref=current.provenance_ref,
            )
            self._pin_concept_publication(session, concept_ref)
            session.replace_annotation(annotation_id, replacement)

        self._mutate(operation)

    def delete_annotation(self, annotation_id: UUID) -> None:
        session = self._require_session()
        if session is not None:
            self._mutate(lambda: session.remove_annotation(annotation_id))

    def undo(self) -> None:
        session = self._require_session()
        if session is not None:
            self._mutate(session.undo)

    def redo(self) -> None:
        session = self._require_session()
        if session is not None:
            self._mutate(session.redo)

    def save(self, *, author: str | None = None, message: str | None = None) -> None:
        session = self._require_session()
        if session is None or self.busy:
            return
        generation = self._generation

        def success(result: SaveResult) -> None:
            if generation != self._generation or self._session is not session:
                return
            if isinstance(result, Saved):
                item = self._catalog_item
                if item is not None:
                    self._catalog_item = RecordingListItem(
                        recording_id=item.recording_id,
                        display_name=session.name,
                        speaker_display_name=item.speaker_display_name,
                        duration_seconds=item.duration_seconds,
                        current_revision_id=result.snapshot.revision.id,
                        revision_number=result.snapshot.revision.number,
                        modified_at=result.snapshot.revision.created_at,
                        audio_status=item.audio_status,
                    )
                self.notice.emit(f"Saved revision {result.snapshot.revision.number}")
            elif isinstance(result, Queued):
                self.notice.emit(f"Save queued as revision {result.revision_id}")
            elif isinstance(result, SaveConflict):
                self.error.emit("Save conflicted with a newer database revision")
            self.session_changed.emit(session)
            self.refresh_recordings()
            self.save_finished.emit(isinstance(result, Saved))

        def failure(exc: BaseException) -> None:
            self.error.emit(f"Could not save recording: {_error_message(exc)}")
            self.save_finished.emit(False)

        self._run(
            lambda: session.save(author=author, message=message),
            success,
            failure,
        )

    def retry_recovery(self) -> None:
        if self.busy:
            return
        session = self._session
        if session is not None and session.dirty:
            self.error.emit("Save your current changes before retrying pending saves")
            self.recovery_finished.emit(False)
            return

        def operation() -> _OpenedRecording | None:
            self._api.recovery.retry_all()
            conflicts = self._api.recovery.list_conflicts()
            pending = self._api.recovery.list_pending()
            if conflicts:
                raise WorkbenchError(
                    "Saved changes conflict with a newer database revision. "
                    "Recovery files are preserved; resolve the conflict before continuing."
                )
            if pending:
                raise WorkbenchError(
                    "Changes are still saved locally only. Check the database connection "
                    "and use Retry Pending Saves again."
                )
            if session is None:
                return None
            opened = self._api.open_recording(session.recording_id)
            try:
                item = next(
                    item
                    for item in self._api.list_recordings(limit=500)
                    if item.recording_id == session.recording_id
                )
                return _OpenedRecording(opened, item, self._resolve_namespaces(opened))
            except BaseException:
                opened.close()
                raise

        def success(opened: _OpenedRecording | None) -> None:
            if opened is not None:
                self.close_session()
                self._install(opened)
            self.refresh_recordings()
            self.notice.emit("Pending saves synchronized with the database")
            self.recovery_finished.emit(True)

        def failure(exc: BaseException) -> None:
            self.recovery_error.emit(f"Could not recover pending saves: {_error_message(exc)}")
            self.recovery_finished.emit(False)

        self._run(operation, success, failure)

    def shutdown(self) -> None:
        self._next_generation()
        session = self._session
        self._session = None
        if session is not None and not session.closed:
            session.close(discard_unsaved_changes=True)

    def _resolve_namespaces(self, session: EditableRecording) -> Mapping[UUID, str]:
        catalog_by_library = {item.library_id: item.namespace for item in self._libraries}
        namespaces: dict[UUID, str] = {}
        for version in session.pinned_libraries:
            namespace = catalog_by_library.get(version.library_id)
            if namespace is None:
                namespace = self._api.libraries.get(version.library_id).namespace
            namespaces[version.id] = namespace
        return MappingProxyType(namespaces)

    def _install_if_current(self, opened: _OpenedRecording, generation: int) -> None:
        if generation != self._generation:
            opened.session.close(discard_unsaved_changes=True)
            return
        self._install(opened)

    def _install(self, opened: _OpenedRecording) -> None:
        self._session = opened.session
        self._catalog_item = opened.catalog_item
        self._namespaces_by_version_id = opened.namespaces_by_version_id
        self.session_changed.emit(opened.session)
        self.notice.emit(f"Opened {opened.session.name}")

    def _require_session(self) -> EditableRecording | None:
        if self._session is None:
            self.error.emit("No recording is open")
        return self._session

    def _mutate(self, operation: Callable[[], object], *, internal: bool = False) -> bool:
        if self.busy and not internal:
            self.error.emit("Wait for the current operation to finish before editing")
            return False
        session = self._session
        if session is None:
            self.error.emit("No recording is open")
            return False
        try:
            operation()
        except (WorkbenchError, ValueError) as exc:
            self.error.emit(_error_message(exc))
            return False
        self.session_changed.emit(session)
        self.notice.emit("Recording updated")
        return True

    def _run(
        self,
        operation: Callable[[], T],
        on_success: Callable[[T], None],
        on_failure: Callable[[BaseException], None],
    ) -> None:
        self._busy_count += 1
        if self._busy_count == 1:
            self.busy_changed.emit(True)

        def success(result: T) -> None:
            try:
                on_success(result)
            finally:
                self._finish_task()

        def failure(exc: BaseException) -> None:
            try:
                on_failure(exc)
            finally:
                self._finish_task()

        self._task_runner.submit(operation, success, failure)

    def _finish_task(self) -> None:
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self.busy_changed.emit(False)

    def _next_generation(self) -> int:
        self._generation += 1
        return self._generation


def _new_geometry(
    geometry_type: str,
    *,
    start_sample: int,
    end_sample: int,
    min_frequency_hz: float,
    max_frequency_hz: float,
) -> PointGeometry | TimeIntervalGeometry | TimeFrequencyBoxGeometry | TimeFrequencyPolygonGeometry:
    if geometry_type == "point":
        return PointGeometry(start_sample=start_sample)
    if geometry_type == "time_interval":
        return TimeIntervalGeometry(start_sample=start_sample, end_sample=end_sample)
    if geometry_type == "time_frequency_box":
        return TimeFrequencyBoxGeometry(
            start_sample=start_sample,
            end_sample=end_sample,
            min_frequency_hz=min_frequency_hz,
            max_frequency_hz=max_frequency_hz,
        )
    raise ValueError(f"unsupported geometry type {geometry_type}")


def _replacement_geometry(
    annotation: SignalAnnotation,
    *,
    start_sample: int,
    end_sample: int,
    min_frequency_hz: float,
    max_frequency_hz: float,
) -> PointGeometry | TimeIntervalGeometry | TimeFrequencyBoxGeometry | TimeFrequencyPolygonGeometry:
    geometry = annotation.geometry
    if isinstance(geometry, PointGeometry):
        return PointGeometry(start_sample=start_sample)
    if isinstance(geometry, TimeIntervalGeometry):
        return TimeIntervalGeometry(start_sample=start_sample, end_sample=end_sample)
    if isinstance(geometry, TimeFrequencyBoxGeometry):
        return TimeFrequencyBoxGeometry(
            start_sample=start_sample,
            end_sample=end_sample,
            min_frequency_hz=min_frequency_hz,
            max_frequency_hz=max_frequency_hz,
        )
    raise ValueError("Polygon editing is not supported")


def _error_message(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(error["msg"] for error in exc.errors(include_url=False)[:3])
    if isinstance(exc, (WorkbenchError, ValueError)):
        return str(exc)
    return "Unexpected application failure"
