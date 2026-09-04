"""In-memory recording editing with local history and coordinated saves."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from application.contracts import (
    AnnotationQuery,
    AudioStorageHandler,
    Queued,
    RecoveryEnvelope,
    RecoveryOutbox,
    ResolvedAudio,
    SaveConflict,
    Saved,
    SaveResult,
    SyncState,
)
from application.errors import (
    AnnotationNotFoundError,
    AudioIntegrityError,
    ConcurrentRevisionError,
    DatabaseUnavailableError,
    DuplicateAnnotationError,
    LibraryVersionInUseError,
    LibraryVersionNotFoundError,
    PendingRecoveryOperationError,
    PersistenceError,
    PersistenceIntegrityError,
    RecoveryStorageError,
    WorkbenchError,
)
from application.persistence import PersistenceStore
from application.read_models import RecordingRevisionSummary, RecordingWorkspace
from application.validation import (
    AnnotationValidationContext,
    LibraryBinding,
    LibraryCatalog,
    bind_library_versions,
    pin_for_version,
    require_library_content_hash,
)
from models import (
    AnnotatedRecordingSnapshot,
    ConceptRef,
    Geometry,
    LibraryEntry,
    LibraryVersion,
    NonEmptyStr,
    PinnedLibraryVersion,
    SaveRecordingSnapshotRequest,
    SignalAnnotation,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
)

_NONEMPTY_STRING = TypeAdapter(NonEmptyStr)


@dataclass(frozen=True, slots=True)
class _EditableState:
    name: str
    language: str
    default_speaker_ref: UUID | None
    bindings: tuple[LibraryBinding, ...]
    annotations: tuple[SignalAnnotation, ...]
    validation: AnnotationValidationContext = field(compare=False, repr=False)
    annotations_by_id: Mapping[UUID, SignalAnnotation] = field(
        compare=False,
        repr=False,
    )


class RecordingEditSession:
    """Own one recording's editable state without retaining a database session."""

    def __init__(
        self,
        *,
        snapshot: AnnotatedRecordingSnapshot,
        audio: ResolvedAudio,
        bindings: tuple[LibraryBinding, ...],
        persistence: PersistenceStore,
        audio_storage: AudioStorageHandler,
        recovery_outbox: RecoveryOutbox,
        library_catalog: LibraryCatalog,
        resolve_versions: Callable[
            [tuple[PinnedLibraryVersion, ...]],
            tuple[LibraryVersion, ...],
        ],
        validation_context: AnnotationValidationContext | None = None,
        sync_state: SyncState = SyncState.SYNCED,
        pending_operation_id: UUID | None = None,
    ) -> None:
        if audio.asset != snapshot.audio_asset:
            raise AudioIntegrityError("resolved audio does not match the recording snapshot")
        self._recording_id = snapshot.recording_id
        self._base_revision_id = snapshot.revision.id
        self._audio = audio
        self._persistence = persistence
        self._audio_storage = audio_storage
        self._recovery_outbox = recovery_outbox
        self._library_catalog = library_catalog
        self._resolve_versions = resolve_versions
        self._state = self._state_from(
            snapshot,
            bindings,
            validation_context=validation_context,
        )
        self._checkpoint = self._state
        self._undo: list[_EditableState] = []
        self._redo: list[_EditableState] = []
        self._sync_state = sync_state
        self._pending_operation_id = pending_operation_id

    @classmethod
    def from_workspace(
        cls,
        workspace: RecordingWorkspace,
        *,
        audio: ResolvedAudio,
        persistence: PersistenceStore,
        audio_storage: AudioStorageHandler,
        recovery_outbox: RecoveryOutbox,
        library_catalog: LibraryCatalog,
        resolve_versions: Callable[
            [tuple[PinnedLibraryVersion, ...]],
            tuple[LibraryVersion, ...],
        ],
    ) -> RecordingEditSession:
        bindings = bind_library_versions(
            workspace.snapshot.libraries,
            workspace.library_versions,
            library_catalog,
        )
        validation_context = AnnotationValidationContext(bindings)
        return cls(
            snapshot=workspace.snapshot,
            audio=audio,
            bindings=bindings,
            persistence=persistence,
            audio_storage=audio_storage,
            recovery_outbox=recovery_outbox,
            library_catalog=library_catalog,
            resolve_versions=resolve_versions,
            validation_context=validation_context,
        )

    @property
    def recording_id(self) -> UUID:
        return self._recording_id

    @property
    def base_revision_id(self) -> UUID:
        return self._base_revision_id

    @property
    def name(self) -> str:
        return self._state.name

    @property
    def language(self) -> str:
        return self._state.language

    @property
    def default_speaker_ref(self) -> UUID | None:
        return self._state.default_speaker_ref

    @property
    def audio(self) -> ResolvedAudio:
        return self._audio

    @property
    def annotations(self) -> tuple[SignalAnnotation, ...]:
        return self._state.annotations

    @property
    def pinned_libraries(self) -> tuple[LibraryVersion, ...]:
        return tuple(binding.version for binding in self._state.bindings)

    @property
    def dirty(self) -> bool:
        return self._state != self._checkpoint

    @property
    def sync_state(self) -> SyncState:
        return self._sync_state

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def set_name(self, name: str) -> None:
        value = _NONEMPTY_STRING.validate_python(name)
        self._mutate(
            _EditableState(
                name=value,
                language=self.language,
                default_speaker_ref=self.default_speaker_ref,
                bindings=self._state.bindings,
                annotations=self.annotations,
                validation=self._state.validation,
                annotations_by_id=self._state.annotations_by_id,
            )
        )

    def set_language(self, language: str) -> None:
        value = _NONEMPTY_STRING.validate_python(language)
        self._mutate(
            _EditableState(
                name=self.name,
                language=value,
                default_speaker_ref=self.default_speaker_ref,
                bindings=self._state.bindings,
                annotations=self.annotations,
                validation=self._state.validation,
                annotations_by_id=self._state.annotations_by_id,
            )
        )

    def set_default_speaker(self, speaker_id: UUID | None) -> None:
        self._mutate(
            _EditableState(
                name=self.name,
                language=self.language,
                default_speaker_ref=speaker_id,
                bindings=self._state.bindings,
                annotations=self.annotations,
                validation=self._state.validation,
                annotations_by_id=self._state.annotations_by_id,
            )
        )

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
        self.add_annotation(annotation)
        return annotation

    def add_annotation(self, annotation: SignalAnnotation) -> None:
        self.add_annotations((annotation,))

    def add_annotations(
        self,
        annotations: Iterable[SignalAnnotation],
    ) -> tuple[SignalAnnotation, ...]:
        additions = tuple(annotations)
        addition_ids: set[UUID] = set()
        for annotation in additions:
            if annotation.id in self._state.annotations_by_id or annotation.id in addition_ids:
                raise DuplicateAnnotationError(
                    f"annotation {annotation.id} already exists in the edit session"
                )
            addition_ids.add(annotation.id)
            self._state.validation.validate(
                annotation,
                audio_asset=self.audio.asset,
            )
        if additions:
            annotations_by_id = dict(self._state.annotations_by_id)
            annotations_by_id.update((annotation.id, annotation) for annotation in additions)
            self._mutate(
                _EditableState(
                    name=self.name,
                    language=self.language,
                    default_speaker_ref=self.default_speaker_ref,
                    bindings=self._state.bindings,
                    annotations=self.annotations + additions,
                    validation=self._state.validation,
                    annotations_by_id=MappingProxyType(annotations_by_id),
                )
            )
        return additions

    def get_annotation(self, annotation_id: UUID) -> SignalAnnotation:
        try:
            return self._state.annotations_by_id[annotation_id]
        except KeyError as exc:
            raise AnnotationNotFoundError(f"annotation {annotation_id} was not found") from exc

    def replace_annotation(
        self,
        annotation_id: UUID,
        replacement: SignalAnnotation,
    ) -> SignalAnnotation:
        if replacement.id != annotation_id:
            raise WorkbenchError("replacement annotation must preserve the target annotation ID")
        current = self.get_annotation(annotation_id)
        if current == replacement:
            return current
        self._state.validation.validate(
            replacement,
            audio_asset=self.audio.asset,
        )
        annotations = list(self.annotations)
        for index, annotation in enumerate(annotations):
            if annotation.id == annotation_id:
                annotations[index] = replacement
                annotations_by_id = dict(self._state.annotations_by_id)
                annotations_by_id[annotation_id] = replacement
                self._mutate(
                    _EditableState(
                        name=self.name,
                        language=self.language,
                        default_speaker_ref=self.default_speaker_ref,
                        bindings=self._state.bindings,
                        annotations=tuple(annotations),
                        validation=self._state.validation,
                        annotations_by_id=MappingProxyType(annotations_by_id),
                    )
                )
                return replacement
        raise PersistenceIntegrityError("annotation index and ordered annotations diverged")

    def remove_annotation(self, annotation_id: UUID) -> SignalAnnotation:
        removed = self.get_annotation(annotation_id)
        annotations = list(self.annotations)
        for index, annotation in enumerate(annotations):
            if annotation.id == annotation_id:
                del annotations[index]
                annotations_by_id = dict(self._state.annotations_by_id)
                del annotations_by_id[annotation_id]
                self._mutate(
                    _EditableState(
                        name=self.name,
                        language=self.language,
                        default_speaker_ref=self.default_speaker_ref,
                        bindings=self._state.bindings,
                        annotations=tuple(annotations),
                        validation=self._state.validation,
                        annotations_by_id=MappingProxyType(annotations_by_id),
                    )
                )
                return removed
        raise PersistenceIntegrityError("annotation index and ordered annotations diverged")

    def find_annotations(self, query: AnnotationQuery) -> tuple[SignalAnnotation, ...]:
        return tuple(
            annotation for annotation in self.annotations if self._matches_query(annotation, query)
        )

    def list_available_concepts(self) -> tuple[LibraryEntry, ...]:
        return self._state.validation.entries

    def resolve_concept(self, concept_ref: ConceptRef) -> LibraryEntry:
        return self._state.validation.resolve(concept_ref)

    def pin_library_version(self, version: LibraryVersion) -> None:
        namespace = self._library_catalog.namespace_for(version)
        require_library_content_hash(version)
        pin = pin_for_version(version, namespace)
        for binding in self._state.bindings:
            if binding.pin.namespace == namespace and binding.pin.version == version.version_label:
                if binding.version == version:
                    return
                raise WorkbenchError(
                    f"{namespace}@{version.version_label} is already pinned with different content"
                )
        bindings = self._state.bindings + (LibraryBinding(pin=pin, version=version),)
        self._mutate(
            _EditableState(
                name=self.name,
                language=self.language,
                default_speaker_ref=self.default_speaker_ref,
                bindings=bindings,
                annotations=self.annotations,
                validation=AnnotationValidationContext(bindings),
                annotations_by_id=self._state.annotations_by_id,
            )
        )

    def unpin_library_version(self, namespace: str, version: str) -> None:
        index = next(
            (
                index
                for index, binding in enumerate(self._state.bindings)
                if binding.pin.namespace == namespace and binding.pin.version == version
            ),
            None,
        )
        if index is None:
            raise LibraryVersionNotFoundError(
                f"pinned library version {namespace}@{version} was not found"
            )
        if any(
            annotation.concept_ref.namespace == namespace
            and annotation.concept_ref.version == version
            for annotation in self.annotations
        ):
            raise LibraryVersionInUseError(
                f"library version {namespace}@{version} is still referenced"
            )
        bindings = list(self._state.bindings)
        del bindings[index]
        updated_bindings = tuple(bindings)
        self._mutate(
            _EditableState(
                name=self.name,
                language=self.language,
                default_speaker_ref=self.default_speaker_ref,
                bindings=updated_bindings,
                annotations=self.annotations,
                validation=AnnotationValidationContext(updated_bindings),
                annotations_by_id=self._state.annotations_by_id,
            )
        )

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._state)
        self._state = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._state)
        self._state = self._redo.pop()
        return True

    def clear_edit_history(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def list_revisions(self) -> tuple[RecordingRevisionSummary, ...]:
        return self._persistence.list_recording_revisions(self.recording_id)

    def load_revision(self, revision_id: UUID) -> AnnotatedRecordingSnapshot:
        return self._persistence.load_snapshot(self.recording_id, revision_id)

    def restore_revision(self, revision_id: UUID) -> None:
        snapshot = self.load_revision(revision_id)
        self._require_same_audio(snapshot)
        versions = self._resolve_versions(tuple(snapshot.libraries))
        bindings = bind_library_versions(
            snapshot.libraries,
            versions,
            self._library_catalog,
        )
        self._mutate(
            self._state_from(
                snapshot,
                bindings,
                validation_context=self._validation_for(bindings),
            )
        )

    def reload(self) -> None:
        self._require_recovery_decision()
        workspace = self._persistence.load_workspace(self.recording_id)
        audio = self._audio_storage.resolve(workspace.snapshot.audio_asset)
        bindings = bind_library_versions(
            workspace.snapshot.libraries,
            workspace.library_versions,
            self._library_catalog,
        )
        self._audio = audio
        self._base_revision_id = workspace.snapshot.revision.id
        self._state = self._state_from(
            workspace.snapshot,
            bindings,
            validation_context=self._validation_for(bindings),
        )
        self._checkpoint = self._state
        self.clear_edit_history()
        self._sync_state = SyncState.SYNCED

    def discard_unsaved_changes(self) -> None:
        self._require_recovery_decision()
        self._state = self._checkpoint
        self.clear_edit_history()

    def save(
        self,
        *,
        author: str | None = None,
        message: str | None = None,
    ) -> SaveResult:
        pending, conflicts = self._active_recovery_operations()
        if self._sync_state is SyncState.CONFLICT or conflicts:
            if conflicts:
                self._sync_state = SyncState.CONFLICT
                self._pending_operation_id = conflicts[-1].operation_id
            raise PendingRecoveryOperationError(
                "the recording has an unresolved recovery conflict; archive it and reload "
                "before saving"
            )
        if self._sync_state is not SyncState.PENDING and pending:
            raise PendingRecoveryOperationError(
                f"recovery operation {pending[-1].operation_id} must be resolved before saving"
            )

        request = SaveRecordingSnapshotRequest(
            recording_id=self.recording_id,
            expected_parent_revision_id=self.base_revision_id,
            name=self.name,
            default_speaker_ref=self.default_speaker_ref,
            language=self.language,
            author=author,
            message=message,
            libraries=tuple(binding.pin for binding in self._state.bindings),
            annotations=self.annotations,
        )
        operation_id = uuid4()
        envelope = RecoveryEnvelope(
            operation_id=operation_id,
            operation_kind="save_recording",
            created_at=datetime.now(UTC),
            request=request,
        )
        self._recovery_outbox.enqueue(envelope)
        if self._sync_state is SyncState.PENDING:
            self._capture_queued_save(request, operation_id)
            return Queued(
                operation_id=operation_id,
                revision_id=request.new_revision_id,
            )
        try:
            snapshot = self._persistence.save_snapshot(request)
        except DatabaseUnavailableError:
            self._capture_queued_save(request, operation_id)
            return Queued(operation_id=operation_id, revision_id=request.new_revision_id)
        except ConcurrentRevisionError:
            current_head = self._current_head()
            self._recovery_outbox.mark_conflict(
                operation_id,
                current_head_revision_id=current_head,
            )
            self._sync_state = SyncState.CONFLICT
            self._pending_operation_id = operation_id
            return SaveConflict(
                operation_id=operation_id,
                expected_head_revision_id=request.expected_parent_revision_id,
                current_head_revision_id=current_head,
            )
        except Exception:
            self._archive_best_effort(operation_id)
            raise

        self._base_revision_id = snapshot.revision.id
        self._checkpoint = self._state
        self._sync_state = SyncState.SYNCED
        self._pending_operation_id = None
        self._mark_applied_best_effort(operation_id)
        return Saved(snapshot=snapshot)

    def _capture_queued_save(
        self,
        request: SaveRecordingSnapshotRequest,
        operation_id: UUID,
    ) -> None:
        self._base_revision_id = request.new_revision_id
        self._checkpoint = self._state
        self._sync_state = SyncState.PENDING
        self._pending_operation_id = operation_id

    def _mutate(self, state: _EditableState) -> None:
        if state == self._state:
            return
        self._undo.append(self._state)
        self._state = state
        self._redo.clear()

    def _require_recovery_decision(self) -> None:
        active = self._recovery_outbox.list_pending() + self._recovery_outbox.list_conflicts()
        operation = next(
            (item for item in active if item.recording_id == self.recording_id),
            None,
        )
        if operation is not None:
            raise PendingRecoveryOperationError(
                f"recovery operation {operation.operation_id} must be retried or archived"
            )
        self._pending_operation_id = None

    def _active_recovery_operations(
        self,
    ) -> tuple[tuple[RecoveryEnvelope, ...], tuple[RecoveryEnvelope, ...]]:
        pending = tuple(
            item
            for item in self._recovery_outbox.list_pending()
            if item.recording_id == self.recording_id
        )
        conflicts = tuple(
            item
            for item in self._recovery_outbox.list_conflicts()
            if item.recording_id == self.recording_id
        )
        return pending, conflicts

    def _current_head(self) -> UUID | None:
        try:
            return self._persistence.load_snapshot(self.recording_id).revision.id
        except PersistenceError:
            return None

    def _mark_applied_best_effort(self, operation_id: UUID) -> None:
        try:
            self._recovery_outbox.mark_applied(operation_id)
        except RecoveryStorageError:
            pass

    def _archive_best_effort(self, operation_id: UUID) -> None:
        try:
            self._recovery_outbox.archive(operation_id)
        except WorkbenchError:
            pass

    def _require_same_audio(self, snapshot: AnnotatedRecordingSnapshot) -> None:
        if (
            snapshot.audio_asset.id != self.audio.asset.id
            or snapshot.audio_asset.sha256 != self.audio.asset.sha256
        ):
            raise AudioIntegrityError("historical revision uses different audio")

    @staticmethod
    def _state_from(
        snapshot: AnnotatedRecordingSnapshot,
        bindings: tuple[LibraryBinding, ...],
        *,
        validation_context: AnnotationValidationContext | None = None,
    ) -> _EditableState:
        if validation_context is None:
            validation_context = AnnotationValidationContext(bindings)
        elif validation_context.bindings != bindings:
            raise PersistenceIntegrityError(
                "annotation validation cache does not match the pinned libraries"
            )
        annotations_by_id = {annotation.id: annotation for annotation in snapshot.annotations}
        if len(annotations_by_id) != len(snapshot.annotations):
            raise PersistenceIntegrityError("annotation IDs are duplicated")
        return _EditableState(
            name=snapshot.name,
            language=snapshot.language,
            default_speaker_ref=snapshot.default_speaker_ref,
            bindings=bindings,
            annotations=snapshot.annotations,
            validation=validation_context,
            annotations_by_id=MappingProxyType(annotations_by_id),
        )

    def _validation_for(
        self,
        bindings: tuple[LibraryBinding, ...],
    ) -> AnnotationValidationContext:
        if self._state.bindings == bindings:
            return self._state.validation
        return AnnotationValidationContext(bindings)

    @staticmethod
    def _matches_query(annotation: SignalAnnotation, query: AnnotationQuery) -> bool:
        if query.concept_refs and annotation.concept_ref not in query.concept_refs:
            return False
        if query.namespaces and annotation.concept_ref.namespace not in query.namespaces:
            return False
        if query.geometry_types and annotation.geometry.type not in query.geometry_types:
            return False

        geometry = annotation.geometry
        start_sample = geometry.start_sample
        end_sample = geometry.end_sample
        if query.start_sample is not None:
            if end_sample is None:
                if start_sample < query.start_sample:
                    return False
            elif end_sample <= query.start_sample:
                return False
        if query.end_sample is not None and start_sample >= query.end_sample:
            return False

        if query.min_frequency_hz is None and query.max_frequency_hz is None:
            return True
        if not isinstance(
            geometry,
            (TimeFrequencyBoxGeometry, TimeFrequencyPolygonGeometry),
        ):
            return False
        if (
            query.min_frequency_hz is not None
            and geometry.max_frequency_hz <= query.min_frequency_hz
        ):
            return False
        return not (
            query.max_frequency_hz is not None
            and geometry.min_frequency_hz >= query.max_frequency_hz
        )
