"""Concrete public handlers for recordings, libraries, speakers, and recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from application.contracts import (
    AudioStorageHandler,
    CreateRecordingCommand,
    PendingSave,
    RecoveryApplied,
    RecoveryConflict,
    RecoveryEnvelope,
    RecoveryOutbox,
    RecoveryResult,
    ResolvedAudio,
    SyncState,
)
from application.edit_session import RecordingEditSession
from application.errors import (
    AudioIntegrityError,
    AudioUnavailableError,
    ConcurrentRevisionError,
    DatabaseUnavailableError,
    InvalidRecoveryEnvelopeError,
    LibraryNotFoundError,
    LibraryVersionNotFoundError,
    PendingRecoveryOperationError,
    PersistenceError,
    RecoveryStorageError,
    WorkbenchError,
)
from application.persistence import PersistenceStore
from application.read_models import (
    AnnotationLibraryListItem,
    AudioAvailability,
    RecordingListItem,
    RecordingRevisionSummary,
)
from application.validation import (
    LibraryCatalog,
    bind_library_versions,
    require_library_content_hash,
    validate_annotations,
)
from models import (
    AnnotatedRecordingSnapshot,
    AudioAsset,
    CreateRecordingRequest,
    Library,
    LibraryVersion,
    PinnedLibraryVersion,
    RevisionMetadata,
    Speaker,
)


class LibraryHandler:
    def __init__(self, persistence: PersistenceStore, catalog: LibraryCatalog) -> None:
        self._persistence = persistence
        self._catalog = catalog

    def list(self) -> tuple[Library, ...]:
        libraries = self._persistence.list_libraries()
        for library in libraries:
            self._catalog.remember_library(library)
        return libraries

    def list_items(self) -> tuple[AnnotationLibraryListItem, ...]:
        return self._persistence.list_annotation_libraries()

    def get(self, library_id: UUID) -> Library:
        library = self._persistence.get_library(library_id)
        self._catalog.remember_library(library)
        return library

    def create(self, library: Library) -> Library:
        created = self._persistence.create_library(library)
        self._catalog.remember_library(created)
        return created

    def list_versions(self, library_id: UUID) -> tuple[LibraryVersion, ...]:
        library = self.get(library_id)
        versions = self._persistence.list_library_versions(library_id)
        for version in versions:
            self._catalog.remember_version(version, library.namespace)
        return versions

    def get_version(self, library_version_id: UUID) -> LibraryVersion:
        version = self._persistence.get_library_version(library_version_id)
        library = self.get(version.library_id)
        self._catalog.remember_version(version, library.namespace)
        return version

    def find_version(self, namespace: str, version: str) -> LibraryVersion:
        cached = self._catalog.cached_version(namespace, version)
        if cached is not None:
            return cached
        library = next(
            (candidate for candidate in self.list() if candidate.namespace == namespace),
            None,
        )
        if library is None:
            raise LibraryNotFoundError(f"library namespace {namespace} was not found")
        match = next(
            (
                candidate
                for candidate in self.list_versions(library.id)
                if candidate.version_label == version
            ),
            None,
        )
        if match is None:
            raise LibraryVersionNotFoundError(
                f"library version {namespace}@{version} was not found"
            )
        return match

    def publish_version(self, version: LibraryVersion) -> LibraryVersion:
        library = self.get(version.library_id)
        require_library_content_hash(version)
        published = self._persistence.publish_library_version(version)
        self._catalog.remember_version(published, library.namespace)
        return published

    def resolve_pins(
        self,
        pins: tuple[PinnedLibraryVersion, ...],
    ) -> tuple[LibraryVersion, ...]:
        versions: list[LibraryVersion] = []
        for pin in pins:
            version = self.find_version(pin.namespace, pin.version)
            if version.content_sha256 != pin.content_sha256:
                raise LibraryVersionNotFoundError(
                    f"library content hash does not match {pin.namespace}@{pin.version}"
                )
            versions.append(version)
        return tuple(versions)


class SpeakerHandler:
    def __init__(self, persistence: PersistenceStore) -> None:
        self._persistence = persistence

    def list(self) -> tuple[Speaker, ...]:
        return self._persistence.list_speakers()

    def get(self, speaker_id: UUID) -> Speaker:
        return self._persistence.get_speaker(speaker_id)

    def create(self, speaker: Speaker) -> Speaker:
        return self._persistence.create_speaker(speaker)


class RecordingHandler:
    def __init__(
        self,
        *,
        persistence: PersistenceStore,
        audio_storage: AudioStorageHandler,
        recovery_outbox: RecoveryOutbox,
        libraries: LibraryHandler,
        library_catalog: LibraryCatalog,
    ) -> None:
        self._persistence = persistence
        self._audio_storage = audio_storage
        self._recovery_outbox = recovery_outbox
        self._libraries = libraries
        self._library_catalog = library_catalog

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[RecordingListItem, ...]:
        records = self._persistence.list_recordings(limit=limit, offset=offset)
        return tuple(
            RecordingListItem(
                recording_id=record.recording_id,
                display_name=record.name,
                speaker_display_name=record.speaker_display_name,
                duration_seconds=record.audio_asset.duration_seconds,
                current_revision_id=record.head_revision_id,
                revision_number=record.revision_number,
                modified_at=record.revised_at,
                audio_status=self._audio_availability(record.audio_asset),
            )
            for record in records
        )

    def create(self, command: CreateRecordingCommand) -> RecordingEditSession:
        audio_asset = self._audio_storage.ingest(
            command.source_audio_path,
            asset_id=uuid4(),
        )
        audio_is_referenced_or_recoverable = False
        try:
            resolved_audio = self._verified_audio(audio_asset)
            versions = self._libraries.resolve_pins(command.libraries)
            bindings = bind_library_versions(
                command.libraries,
                versions,
                self._library_catalog,
            )
            validation_context = validate_annotations(
                command.annotations,
                audio_asset=audio_asset,
                bindings=bindings,
            )
            request = CreateRecordingRequest(
                recording_id=command.recording_id,
                initial_revision_id=command.initial_revision_id,
                audio_asset=audio_asset,
                name=command.name,
                default_speaker_ref=command.default_speaker_ref,
                language=command.language,
                author=command.author,
                message=command.message,
                libraries=command.libraries,
                annotations=command.annotations,
            )
            operation_id = uuid4()
            envelope = RecoveryEnvelope(
                operation_id=operation_id,
                operation_kind="create_recording",
                created_at=datetime.now(UTC),
                request=request,
            )
            self._recovery_outbox.enqueue(envelope)
            sync_state = SyncState.SYNCED
            pending_operation_id: UUID | None = None
            try:
                snapshot = self._persistence.create_recording(request)
            except DatabaseUnavailableError:
                snapshot = self._pending_creation_snapshot(request)
                sync_state = SyncState.PENDING
                pending_operation_id = operation_id
                audio_is_referenced_or_recoverable = True
            except Exception:
                self._archive_best_effort(operation_id)
                raise
            else:
                audio_is_referenced_or_recoverable = True
                self._mark_applied_best_effort(operation_id)

            return RecordingEditSession(
                snapshot=snapshot,
                audio=resolved_audio,
                bindings=bindings,
                persistence=self._persistence,
                audio_storage=self._audio_storage,
                recovery_outbox=self._recovery_outbox,
                library_catalog=self._library_catalog,
                resolve_versions=self._libraries.resolve_pins,
                validation_context=validation_context,
                sync_state=sync_state,
                pending_operation_id=pending_operation_id,
            )
        except Exception as exc:
            if not audio_is_referenced_or_recoverable:
                try:
                    self._audio_storage.discard(audio_asset)
                except WorkbenchError as cleanup_error:
                    exc.add_note(f"managed audio cleanup also failed: {cleanup_error}")
            raise

    def open(self, recording_id: UUID) -> RecordingEditSession:
        active = next(
            (
                envelope
                for envelope in (
                    self._recovery_outbox.list_pending() + self._recovery_outbox.list_conflicts()
                )
                if envelope.recording_id == recording_id
            ),
            None,
        )
        if active is not None:
            raise PendingRecoveryOperationError(
                f"recovery operation {active.operation_id} must be resolved before opening "
                f"recording {recording_id}"
            )
        workspace = self._persistence.load_workspace(recording_id)
        audio = self._verified_audio(workspace.snapshot.audio_asset)
        return RecordingEditSession.from_workspace(
            workspace,
            audio=audio,
            persistence=self._persistence,
            audio_storage=self._audio_storage,
            recovery_outbox=self._recovery_outbox,
            library_catalog=self._library_catalog,
            resolve_versions=self._libraries.resolve_pins,
        )

    def list_revisions(
        self,
        recording_id: UUID,
    ) -> tuple[RecordingRevisionSummary, ...]:
        return self._persistence.list_recording_revisions(recording_id)

    def load_revision(
        self,
        recording_id: UUID,
        revision_id: UUID,
    ) -> AnnotatedRecordingSnapshot:
        return self._persistence.load_snapshot(recording_id, revision_id)

    @staticmethod
    def _pending_creation_snapshot(
        request: CreateRecordingRequest,
    ) -> AnnotatedRecordingSnapshot:
        return AnnotatedRecordingSnapshot(
            recording_id=request.recording_id,
            revision=RevisionMetadata(
                id=request.initial_revision_id,
                number=1,
                parent_id=None,
                created_at=datetime.now(UTC),
                author=request.author,
                message=request.message,
            ),
            name=request.name,
            default_speaker_ref=request.default_speaker_ref,
            language=request.language,
            audio_asset=request.audio_asset,
            libraries=request.libraries,
            annotations=request.annotations,
        )

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

    def _audio_availability(self, audio_asset: AudioAsset) -> AudioAvailability:
        try:
            self._audio_storage.resolve(audio_asset)
        except AudioUnavailableError:
            return AudioAvailability.MISSING
        except WorkbenchError:
            return AudioAvailability.INVALID
        return AudioAvailability.AVAILABLE

    def _verified_audio(self, audio_asset: AudioAsset) -> ResolvedAudio:
        verification = self._audio_storage.verify(audio_asset)
        asset_id = verification.asset.id
        if not verification.exists or verification.local_path is None:
            raise AudioUnavailableError(f"audio asset {asset_id} is unavailable")
        if not verification.hash_matches:
            raise AudioIntegrityError(f"audio asset {asset_id} failed SHA-256 verification")
        return ResolvedAudio(asset=verification.asset, local_path=verification.local_path)


class RecoveryHandler:
    def __init__(
        self,
        persistence: PersistenceStore,
        recovery_outbox: RecoveryOutbox,
    ) -> None:
        self._persistence = persistence
        self._recovery_outbox = recovery_outbox

    def list_pending(self) -> tuple[PendingSave, ...]:
        return tuple(self._pending(envelope) for envelope in self._recovery_outbox.list_pending())

    def list_conflicts(self) -> tuple[RecoveryConflict, ...]:
        return tuple(
            self._conflict(envelope) for envelope in self._recovery_outbox.list_conflicts()
        )

    def retry(self, operation_id: UUID) -> RecoveryResult:
        envelope = self._recovery_outbox.get_active(operation_id)
        if envelope is None:
            raise InvalidRecoveryEnvelopeError(
                f"active recovery operation {operation_id} was not found"
            )
        try:
            if isinstance(envelope.request, CreateRecordingRequest):
                snapshot = self._persistence.create_recording(envelope.request)
            else:
                snapshot = self._persistence.save_snapshot(envelope.request)
        except DatabaseUnavailableError:
            return self._pending(envelope)
        except ConcurrentRevisionError:
            current_head = self._current_head(envelope.recording_id)
            self._recovery_outbox.mark_conflict(
                operation_id,
                current_head_revision_id=current_head,
            )
            return RecoveryConflict(
                operation_id=envelope.operation_id,
                recording_id=envelope.recording_id,
                revision_id=envelope.revision_id,
                expected_head_revision_id=envelope.expected_head_revision_id,
                current_head_revision_id=current_head,
                created_at=envelope.created_at,
            )

        self._recovery_outbox.mark_applied(operation_id)
        return RecoveryApplied(
            operation_id=envelope.operation_id,
            recording_id=envelope.recording_id,
            revision_id=envelope.revision_id,
            snapshot=snapshot,
        )

    def retry_all(self) -> tuple[RecoveryResult, ...]:
        results: list[RecoveryResult] = []
        blocked_recordings = {
            envelope.recording_id for envelope in self._recovery_outbox.list_conflicts()
        }
        remaining = list(self._recovery_outbox.list_pending())
        while remaining:
            pending_revision_ids = {(item.recording_id, item.revision_id) for item in remaining}
            ready_index = next(
                (
                    index
                    for index, item in enumerate(remaining)
                    if item.recording_id not in blocked_recordings
                    and (
                        item.expected_head_revision_id is None
                        or (item.recording_id, item.expected_head_revision_id)
                        not in pending_revision_ids
                    )
                ),
                None,
            )
            if ready_index is None:
                if any(item.recording_id not in blocked_recordings for item in remaining):
                    raise InvalidRecoveryEnvelopeError(
                        "pending recovery operations contain a dependency cycle"
                    )
                break
            envelope = remaining.pop(ready_index)
            result = self.retry(envelope.operation_id)
            results.append(result)
            if isinstance(result, (PendingSave, RecoveryConflict)):
                blocked_recordings.add(envelope.recording_id)
        return tuple(results)

    def archive(self, operation_id: UUID) -> None:
        self._recovery_outbox.archive(operation_id)

    @staticmethod
    def _pending(envelope: RecoveryEnvelope) -> PendingSave:
        return PendingSave(
            operation_id=envelope.operation_id,
            operation_kind=envelope.operation_kind,
            recording_id=envelope.recording_id,
            revision_id=envelope.revision_id,
            expected_head_revision_id=envelope.expected_head_revision_id,
            created_at=envelope.created_at,
        )

    @staticmethod
    def _conflict(envelope: RecoveryEnvelope) -> RecoveryConflict:
        return RecoveryConflict(
            operation_id=envelope.operation_id,
            recording_id=envelope.recording_id,
            revision_id=envelope.revision_id,
            expected_head_revision_id=envelope.expected_head_revision_id,
            current_head_revision_id=envelope.current_head_revision_id,
            created_at=envelope.created_at,
        )

    def _current_head(self, recording_id: UUID) -> UUID | None:
        try:
            return self._persistence.load_snapshot(recording_id).revision.id
        except PersistenceError:
            return None
