from __future__ import annotations

import wave
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import application.validation as validation_module
from application import (
    AnnotationQuery,
    AudioUnavailableError,
    ConceptNotFoundError,
    ConceptNotPinnedError,
    CreateRecordingCommand,
    DatabaseUnavailableError,
    DuplicateAnnotationError,
    FileRecoveryOutbox,
    GeometryNotAllowedError,
    GeometryOutOfBoundsError,
    InvalidAnnotationAttributesError,
    LibraryVersionInUseError,
    LocalAudioStorage,
    PendingRecoveryOperationError,
    Queued,
    RecoveryApplied,
    RecoveryConflict,
    SaveConflict,
    Saved,
    SyncState,
    UnsupportedAudioError,
    create_workbench,
    library_content_sha256,
)
from application.errors import (
    AudioAssetNotFoundError,
    ConcurrentRevisionError,
    LibraryNotFoundError,
    LibraryVersionNotFoundError,
    PersistenceIntegrityError,
    RecordingNotFoundError,
    RevisionNotFoundError,
    SpeakerNotFoundError,
)
from application.read_models import (
    RecordingRevisionSummary,
    RecordingSummary,
    RecordingWorkspace,
)
from models import (
    AnnotatedRecordingSnapshot,
    AudioAsset,
    ConceptRef,
    CreateRecordingRequest,
    Library,
    LibraryEntry,
    LibraryVersion,
    PinnedLibraryVersion,
    PointGeometry,
    RevisionMetadata,
    SaveRecordingSnapshotRequest,
    SignalAnnotation,
    Speaker,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
    TimeFrequencyVertex,
    TimeIntervalGeometry,
)


class MemoryPersistenceStore:
    """Small contract-faithful store used to exercise the application boundary."""

    def __init__(self) -> None:
        self.audio_assets: dict[UUID, AudioAsset] = {}
        self.speakers: dict[UUID, Speaker] = {}
        self.libraries: dict[UUID, Library] = {}
        self.versions: dict[UUID, LibraryVersion] = {}
        self.snapshots: dict[UUID, dict[UUID, AnnotatedRecordingSnapshot]] = {}
        self.heads: dict[UUID, UUID] = {}
        self.calls: list[str] = []
        self.offline = False
        self.fail_after_commit_once = False

    def _call(self, name: str) -> None:
        self.calls.append(name)

    def _require_online(self) -> None:
        if self.offline:
            raise DatabaseUnavailableError("database is offline")

    def register_audio_asset(self, audio_asset: AudioAsset) -> AudioAsset:
        self._call("register_audio_asset")
        self._require_online()
        existing = self.audio_assets.get(audio_asset.id)
        if existing is not None and existing != audio_asset:
            raise PersistenceIntegrityError("audio asset ID has different content")
        self.audio_assets[audio_asset.id] = audio_asset
        return audio_asset

    def get_audio_asset(self, audio_asset_id: UUID) -> AudioAsset:
        self._call("get_audio_asset")
        self._require_online()
        try:
            return self.audio_assets[audio_asset_id]
        except KeyError as exc:
            raise AudioAssetNotFoundError(str(audio_asset_id)) from exc

    def create_speaker(self, speaker: Speaker) -> Speaker:
        self._call("create_speaker")
        self._require_online()
        self.speakers[speaker.id] = speaker
        return speaker

    def get_speaker(self, speaker_id: UUID) -> Speaker:
        self._call("get_speaker")
        self._require_online()
        try:
            return self.speakers[speaker_id]
        except KeyError as exc:
            raise SpeakerNotFoundError(str(speaker_id)) from exc

    def list_speakers(self) -> tuple[Speaker, ...]:
        self._call("list_speakers")
        self._require_online()
        return tuple(sorted(self.speakers.values(), key=lambda item: str(item.id)))

    def create_library(self, library: Library) -> Library:
        self._call("create_library")
        self._require_online()
        self.libraries[library.id] = library
        return library

    def get_library(self, library_id: UUID) -> Library:
        self._call("get_library")
        self._require_online()
        try:
            return self.libraries[library_id]
        except KeyError as exc:
            raise LibraryNotFoundError(str(library_id)) from exc

    def list_libraries(self) -> tuple[Library, ...]:
        self._call("list_libraries")
        self._require_online()
        return tuple(sorted(self.libraries.values(), key=lambda item: item.namespace))

    def publish_library_version(self, version: LibraryVersion) -> LibraryVersion:
        self._call("publish_library_version")
        self._require_online()
        self.versions[version.id] = version
        return version

    def get_library_version(self, library_version_id: UUID) -> LibraryVersion:
        self._call("get_library_version")
        self._require_online()
        try:
            return self.versions[library_version_id]
        except KeyError as exc:
            raise LibraryVersionNotFoundError(str(library_version_id)) from exc

    def list_library_versions(self, library_id: UUID) -> tuple[LibraryVersion, ...]:
        self._call("list_library_versions")
        self._require_online()
        return tuple(
            sorted(
                (version for version in self.versions.values() if version.library_id == library_id),
                key=lambda item: item.version_label,
            )
        )

    def list_recordings(self, *, limit: int, offset: int) -> tuple[RecordingSummary, ...]:
        self._call("list_recordings")
        self._require_online()
        snapshots = [
            self.snapshots[recording_id][head_id] for recording_id, head_id in self.heads.items()
        ]
        summaries = sorted(snapshots, key=lambda item: (item.name, str(item.recording_id)))
        return tuple(
            RecordingSummary(
                recording_id=snapshot.recording_id,
                head_revision_id=snapshot.revision.id,
                name=snapshot.name,
                language=snapshot.language,
                audio_storage_uri=snapshot.audio_asset.storage_uri,
                duration_seconds=snapshot.audio_asset.duration_seconds,
                revision_number=snapshot.revision.number,
                revised_at=snapshot.revision.created_at,
            )
            for snapshot in summaries[offset : offset + limit]
        )

    def load_snapshot(
        self,
        recording_id: UUID,
        revision_id: UUID | None = None,
    ) -> AnnotatedRecordingSnapshot:
        self._call("load_snapshot")
        self._require_online()
        if recording_id not in self.snapshots:
            raise RecordingNotFoundError(str(recording_id))
        selected = revision_id or self.heads[recording_id]
        try:
            return self.snapshots[recording_id][selected]
        except KeyError as exc:
            raise RevisionNotFoundError(str(selected)) from exc

    def load_workspace(
        self,
        recording_id: UUID,
        revision_id: UUID | None = None,
    ) -> RecordingWorkspace:
        self._call("load_workspace")
        snapshot = self.load_snapshot(recording_id, revision_id)
        return RecordingWorkspace(
            snapshot=snapshot,
            default_speaker=(
                self.speakers.get(snapshot.default_speaker_ref)
                if snapshot.default_speaker_ref is not None
                else None
            ),
            library_versions=tuple(self._version_for_pin(pin) for pin in snapshot.libraries),
        )

    def create_recording(
        self,
        request: CreateRecordingRequest,
    ) -> AnnotatedRecordingSnapshot:
        self._call("create_recording")
        self._require_online()
        existing = self.snapshots.get(request.recording_id, {}).get(request.initial_revision_id)
        if existing is not None:
            return existing
        if request.recording_id in self.snapshots:
            raise PersistenceIntegrityError("recording already exists")
        self.audio_assets[request.audio_asset.id] = request.audio_asset
        snapshot = AnnotatedRecordingSnapshot(
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
        self.snapshots[request.recording_id] = {snapshot.revision.id: snapshot}
        self.heads[request.recording_id] = snapshot.revision.id
        self._fail_after_commit_if_requested()
        return snapshot

    def save_snapshot(
        self,
        request: SaveRecordingSnapshotRequest,
    ) -> AnnotatedRecordingSnapshot:
        self._call("save_snapshot")
        self._require_online()
        recording_snapshots = self.snapshots.get(request.recording_id)
        if recording_snapshots is None:
            raise RecordingNotFoundError(str(request.recording_id))
        existing = recording_snapshots.get(request.new_revision_id)
        if existing is not None:
            return existing
        if self.heads[request.recording_id] != request.expected_parent_revision_id:
            raise ConcurrentRevisionError("recording head changed")
        parent = recording_snapshots[self.heads[request.recording_id]]
        snapshot = AnnotatedRecordingSnapshot(
            recording_id=request.recording_id,
            revision=RevisionMetadata(
                id=request.new_revision_id,
                number=parent.revision.number + 1,
                parent_id=parent.revision.id,
                created_at=datetime.now(UTC),
                author=request.author,
                message=request.message,
            ),
            name=request.name,
            default_speaker_ref=request.default_speaker_ref,
            language=request.language,
            audio_asset=parent.audio_asset,
            libraries=request.libraries,
            annotations=request.annotations,
        )
        recording_snapshots[snapshot.revision.id] = snapshot
        self.heads[request.recording_id] = snapshot.revision.id
        self._fail_after_commit_if_requested()
        return snapshot

    def list_recording_revisions(
        self,
        recording_id: UUID,
    ) -> tuple[RecordingRevisionSummary, ...]:
        self._call("list_recording_revisions")
        self._require_online()
        if recording_id not in self.snapshots:
            raise RecordingNotFoundError(str(recording_id))
        snapshots = sorted(
            self.snapshots[recording_id].values(),
            key=lambda item: item.revision.number,
        )
        return tuple(
            RecordingRevisionSummary(
                metadata=snapshot.revision,
                annotation_count=len(snapshot.annotations),
            )
            for snapshot in snapshots
        )

    def _version_for_pin(self, pin: PinnedLibraryVersion) -> LibraryVersion:
        for version in self.versions.values():
            library = self.libraries[version.library_id]
            if (
                library.namespace == pin.namespace
                and version.version_label == pin.version
                and version.content_sha256 == pin.content_sha256
            ):
                return version
        raise LibraryVersionNotFoundError(str(pin))

    def _fail_after_commit_if_requested(self) -> None:
        if self.fail_after_commit_once:
            self.fail_after_commit_once = False
            raise DatabaseUnavailableError("connection dropped after commit")


def write_wav(path: Path, *, sample_rate_hz: int = 8_000, frame_count: int = 800) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate_hz)
        output.writeframes(b"\x00\x00" * frame_count)


def publish_test_library(api: object) -> tuple[LibraryVersion, PinnedLibraryVersion]:
    libraries = api.libraries  # type: ignore[attr-defined]
    library = libraries.create(Library(id=uuid4(), namespace="le.test", name="Test annotations"))
    version_id = uuid4()
    entry = LibraryEntry(
        id=uuid4(),
        library_version_id=version_id,
        entry_key="event",
        display_name="Event",
        description="A test event.",
        allowed_geometry_types=(
            "time_interval",
            "time_frequency_box",
            "time_frequency_polygon",
        ),
        attribute_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        },
    )
    draft = LibraryVersion(
        id=version_id,
        library_id=library.id,
        version_label="1.0.0",
        content_sha256="0" * 64,
        created_at=datetime.now(UTC),
        entries=(entry,),
    )
    version = draft.model_copy(update={"content_sha256": library_content_sha256(draft)})
    published = libraries.publish_version(version)
    return published, PinnedLibraryVersion(
        namespace=library.namespace,
        version=published.version_label,
        content_sha256=published.content_sha256,
    )


@pytest.fixture
def application_system(tmp_path: Path) -> tuple[object, MemoryPersistenceStore, Path, Path]:
    store = MemoryPersistenceStore()
    audio_root = tmp_path / "audio"
    recovery_root = tmp_path / "recovery"
    api = create_workbench(
        persistence=store,
        audio_storage=LocalAudioStorage(audio_root),
        recovery_outbox=FileRecoveryOutbox(recovery_root),
    )
    source = tmp_path / "source.wav"
    write_wav(source)
    return api, store, source, recovery_root


def create_recording(
    api: object,
    source: Path,
    *,
    libraries: tuple[PinnedLibraryVersion, ...] = (),
    name: str = "Recording",
) -> object:
    return api.recordings.create(  # type: ignore[attr-defined,no-any-return]
        CreateRecordingCommand(
            recording_id=uuid4(),
            source_audio_path=source,
            name=name,
            language="en",
            libraries=libraries,
        )
    )


def annotation(
    concept_ref: ConceptRef,
    *,
    start: int = 10,
    end: int = 20,
    label: object = "valid",
) -> SignalAnnotation:
    return SignalAnnotation(
        id=uuid4(),
        concept_ref=concept_ref,
        geometry=TimeIntervalGeometry(start_sample=start, end_sample=end),
        attributes={"label": label},
    )


def test_public_handlers_create_edit_save_and_reopen(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, store, source, recovery_root = application_system
    speaker = api.speakers.create(  # type: ignore[attr-defined]
        Speaker(id=uuid4(), display_name="Speaker")
    )
    assert api.speakers.get(speaker.id) == speaker  # type: ignore[attr-defined]
    assert api.speakers.list() == (speaker,)  # type: ignore[attr-defined]
    version, pin = publish_test_library(api)

    recording_id = uuid4()
    session = api.recordings.create(  # type: ignore[attr-defined]
        CreateRecordingCommand(
            recording_id=recording_id,
            source_audio_path=source,
            name="Original",
            language="en",
            default_speaker_ref=speaker.id,
            libraries=(pin,),
        )
    )

    assert session.sync_state is SyncState.SYNCED
    assert not session.dirty
    assert session.audio.asset.id != recording_id
    assert session.audio.asset.sample_rate_hz == 8_000
    assert session.audio.asset.frame_count == 800
    assert session.pinned_libraries == (version,)
    created = session.create_annotation(
        concept_ref=ConceptRef("le.test@1.0.0:event"),
        geometry=TimeIntervalGeometry(start_sample=10, end_sample=50),
        attributes={"label": "first"},
    )
    session.set_name("Edited")

    result = session.save(author="tester", message="save")

    assert isinstance(result, Saved)
    assert result.snapshot.annotations == (created,)
    assert result.snapshot.revision.parent_id is not None
    assert not session.dirty
    reopened = api.recordings.open(recording_id)  # type: ignore[attr-defined]
    assert reopened.name == "Edited"
    assert reopened.annotations == (created,)
    assert len(api.recordings.list()) == 1  # type: ignore[attr-defined]
    assert len(api.recordings.list_revisions(recording_id)) == 2  # type: ignore[attr-defined]
    assert len(tuple((recovery_root / "applied").glob("*.json"))) == 2
    assert store.heads[recording_id] == result.snapshot.revision.id


def test_session_mutations_are_atomic_validated_and_database_free(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, store, source, _ = application_system
    version, pin = publish_test_library(api)
    session = create_recording(api, source, libraries=(pin,))
    concept = ConceptRef("le.test@1.0.0:event")
    calls_before_edits = len(store.calls)

    first = annotation(concept, start=10, end=30)
    session.add_annotation(first)  # type: ignore[attr-defined]
    with pytest.raises(DuplicateAnnotationError):
        session.add_annotations((annotation(concept), first))  # type: ignore[attr-defined]
    assert session.annotations == (first,)  # type: ignore[attr-defined]

    with pytest.raises(ConceptNotPinnedError):
        session.add_annotation(annotation(ConceptRef("other@1.0.0:event")))  # type: ignore[attr-defined]
    with pytest.raises(ConceptNotFoundError):
        session.add_annotation(annotation(ConceptRef("le.test@1.0.0:missing")))  # type: ignore[attr-defined]
    with pytest.raises(GeometryNotAllowedError):
        session.create_annotation(  # type: ignore[attr-defined]
            concept_ref=concept,
            geometry=PointGeometry(start_sample=10),
            attributes={"label": "point"},
        )
    with pytest.raises(GeometryOutOfBoundsError):
        session.add_annotation(annotation(concept, end=801))  # type: ignore[attr-defined]
    with pytest.raises(GeometryOutOfBoundsError):
        session.create_annotation(  # type: ignore[attr-defined]
            concept_ref=concept,
            geometry=TimeFrequencyBoxGeometry(
                start_sample=10,
                end_sample=20,
                min_frequency_hz=100,
                max_frequency_hz=4_001,
            ),
            attributes={"label": "high"},
        )
    with pytest.raises(InvalidAnnotationAttributesError):
        session.add_annotation(annotation(concept, label=7))  # type: ignore[attr-defined]

    replacement = first.model_copy(update={"note": "changed"})
    assert session.replace_annotation(first.id, replacement) == replacement  # type: ignore[attr-defined]
    assert session.undo()  # type: ignore[attr-defined]
    assert session.get_annotation(first.id) == first  # type: ignore[attr-defined]
    assert session.redo()  # type: ignore[attr-defined]
    assert session.remove_annotation(first.id) == replacement  # type: ignore[attr-defined]
    with pytest.raises(LibraryVersionInUseError):
        session.add_annotation(first)  # type: ignore[attr-defined]
        session.unpin_library_version("le.test", "1.0.0")  # type: ignore[attr-defined]
    session.remove_annotation(first.id)  # type: ignore[attr-defined]
    session.unpin_library_version("le.test", "1.0.0")  # type: ignore[attr-defined]
    session.pin_library_version(version)  # type: ignore[attr-defined]
    assert len(store.calls) == calls_before_edits


def test_annotation_queries_use_intersection_and_frequency_filters(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, _, source, _ = application_system
    _, pin = publish_test_library(api)
    session = create_recording(api, source, libraries=(pin,))
    concept = ConceptRef("le.test@1.0.0:event")
    interval = annotation(concept, start=10, end=20)
    box = SignalAnnotation(
        id=uuid4(),
        concept_ref=concept,
        geometry=TimeFrequencyBoxGeometry(
            start_sample=30,
            end_sample=50,
            min_frequency_hz=100,
            max_frequency_hz=500,
        ),
        attributes={"label": "box"},
    )
    session.add_annotations((interval, box))  # type: ignore[attr-defined]

    assert session.find_annotations(  # type: ignore[attr-defined]
        AnnotationQuery(start_sample=15, end_sample=35)
    ) == (interval, box)
    assert session.find_annotations(  # type: ignore[attr-defined]
        AnnotationQuery(min_frequency_hz=200, max_frequency_hz=300)
    ) == (box,)
    assert session.find_annotations(  # type: ignore[attr-defined]
        AnnotationQuery(geometry_types=frozenset({"time_interval"}))
    ) == (interval,)


def test_offline_save_and_ambiguous_commit_retry_idempotently(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, store, source, _ = application_system
    session = create_recording(api, source)
    recording_id = session.recording_id  # type: ignore[attr-defined]
    session.set_name("Queued")  # type: ignore[attr-defined]
    store.offline = True

    queued = session.save()  # type: ignore[attr-defined]

    assert isinstance(queued, Queued)
    assert session.sync_state is SyncState.PENDING  # type: ignore[attr-defined]
    assert not session.dirty  # type: ignore[attr-defined]
    with pytest.raises(PendingRecoveryOperationError):
        session.reload()  # type: ignore[attr-defined]
    store.offline = False
    applied = api.recovery.retry(queued.operation_id)  # type: ignore[attr-defined]
    assert isinstance(applied, RecoveryApplied)
    assert applied.revision_id == queued.revision_id
    session.reload()  # type: ignore[attr-defined]
    assert session.name == "Queued"  # type: ignore[attr-defined]

    session.set_name("Committed before disconnect")  # type: ignore[attr-defined]
    store.fail_after_commit_once = True
    ambiguous = session.save()  # type: ignore[attr-defined]
    assert isinstance(ambiguous, Queued)
    assert store.heads[recording_id] == ambiguous.revision_id

    retried = api.recovery.retry(ambiguous.operation_id)  # type: ignore[attr-defined]
    assert isinstance(retried, RecoveryApplied)
    assert retried.revision_id == ambiguous.revision_id
    assert len(store.snapshots[recording_id]) == 3


def test_stale_save_becomes_explicit_conflict_until_archived(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, _, source, _ = application_system
    original = create_recording(api, source)
    first = api.recordings.open(original.recording_id)  # type: ignore[attr-defined]
    second = api.recordings.open(original.recording_id)  # type: ignore[attr-defined]
    first.set_name("Winner")
    assert isinstance(first.save(), Saved)
    second.set_name("Stale")

    conflict = second.save()

    assert isinstance(conflict, SaveConflict)
    assert second.sync_state is SyncState.CONFLICT
    listed = api.recovery.list_conflicts()  # type: ignore[attr-defined]
    assert len(listed) == 1
    assert isinstance(api.recovery.retry(conflict.operation_id), RecoveryConflict)  # type: ignore[attr-defined]
    with pytest.raises(PendingRecoveryOperationError):
        second.save()
    with pytest.raises(PendingRecoveryOperationError):
        second.reload()
    api.recovery.archive(conflict.operation_id)  # type: ignore[attr-defined]
    second.reload()
    assert second.name == "Winner"
    assert second.sync_state is SyncState.SYNCED


def test_offline_creation_is_recoverable(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, store, source, _ = application_system
    store.offline = True

    session = create_recording(api, source)

    assert session.sync_state is SyncState.PENDING  # type: ignore[attr-defined]
    pending = api.recovery.list_pending()  # type: ignore[attr-defined]
    assert len(pending) == 1
    store.offline = False
    result = api.recovery.retry(pending[0].operation_id)  # type: ignore[attr-defined]
    assert isinstance(result, RecoveryApplied)
    session.reload()  # type: ignore[attr-defined]
    assert session.sync_state is SyncState.SYNCED  # type: ignore[attr-defined]


def test_local_audio_storage_verifies_integrity_and_rejects_unsupported_files(
    tmp_path: Path,
) -> None:
    storage = LocalAudioStorage(tmp_path / "audio")
    source = tmp_path / "source.wav"
    write_wav(source)
    asset = storage.ingest(source, asset_id=uuid4())

    assert storage.resolve(asset).local_path.is_file()
    assert storage.verify(asset).hash_matches
    storage.resolve(asset).local_path.write_bytes(b"changed")
    verification = storage.verify(asset)
    assert verification.exists
    assert not verification.hash_matches

    unsupported = tmp_path / "audio.mp3"
    unsupported.write_bytes(b"not audio")
    with pytest.raises(UnsupportedAudioError):
        storage.ingest(unsupported, asset_id=uuid4())


def test_recovery_outbox_quarantines_malformed_envelopes(tmp_path: Path) -> None:
    outbox = FileRecoveryOutbox(tmp_path / "recovery")
    malformed = outbox.root / "pending" / f"{uuid4()}.json"
    malformed.write_text("{not valid JSON", encoding="utf-8")

    assert outbox.list_pending() == ()
    assert not malformed.exists()
    assert len(tuple((outbox.root / "quarantine").glob("*.json"))) == 1


def test_polygon_above_nyquist_is_rejected(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, _, source, _ = application_system
    _, pin = publish_test_library(api)
    session = create_recording(api, source, libraries=(pin,))
    polygon = TimeFrequencyPolygonGeometry(
        start_sample=10,
        end_sample=20,
        min_frequency_hz=100,
        max_frequency_hz=4_100,
        vertices=(
            TimeFrequencyVertex(sample=10, frequency_hz=100),
            TimeFrequencyVertex(sample=20, frequency_hz=100),
            TimeFrequencyVertex(sample=15, frequency_hz=4_100),
        ),
    )

    with pytest.raises(GeometryOutOfBoundsError):
        session.create_annotation(  # type: ignore[attr-defined]
            concept_ref=ConceptRef("le.test@1.0.0:event"),
            geometry=polygon,
            attributes={"label": "polygon"},
        )


def test_historical_restore_saves_as_a_child_of_the_current_head(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, _, source, _ = application_system
    session = create_recording(api, source, name="Original")
    initial_revision_id = session.base_revision_id  # type: ignore[attr-defined]
    session.set_name("Second")  # type: ignore[attr-defined]
    second = session.save()  # type: ignore[attr-defined]
    assert isinstance(second, Saved)

    session.restore_revision(initial_revision_id)  # type: ignore[attr-defined]

    assert session.name == "Original"  # type: ignore[attr-defined]
    assert session.base_revision_id == second.snapshot.revision.id  # type: ignore[attr-defined]
    assert session.dirty  # type: ignore[attr-defined]
    restored = session.save(message="restore original")  # type: ignore[attr-defined]
    assert isinstance(restored, Saved)
    assert restored.snapshot.revision.parent_id == second.snapshot.revision.id
    assert restored.snapshot.name == "Original"


def test_open_reports_missing_audio_as_a_typed_error(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, _, source, _ = application_system
    session = create_recording(api, source)
    session.audio.local_path.unlink()  # type: ignore[attr-defined]

    with pytest.raises(AudioUnavailableError):
        api.recordings.open(session.recording_id)  # type: ignore[attr-defined]


def test_retry_all_applies_dependent_queued_saves_in_parent_order(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, store, source, _ = application_system
    session = create_recording(api, source)
    store.offline = True
    session.set_name("First queued")  # type: ignore[attr-defined]
    first = session.save()  # type: ignore[attr-defined]
    save_attempts_before_child = store.calls.count("save_snapshot")
    store.offline = False
    session.set_name("Second queued")  # type: ignore[attr-defined]
    second = session.save()  # type: ignore[attr-defined]
    assert isinstance(first, Queued)
    assert isinstance(second, Queued)
    assert store.calls.count("save_snapshot") == save_attempts_before_child

    results = api.recovery.retry_all()  # type: ignore[attr-defined]

    assert [result.revision_id for result in results] == [
        first.revision_id,
        second.revision_id,
    ]
    assert all(isinstance(result, RecoveryApplied) for result in results)
    assert store.heads[session.recording_id] == second.revision_id  # type: ignore[attr-defined]


def test_restart_refuses_to_open_a_recording_with_pending_recovery(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, store, source, recovery_root = application_system
    session = create_recording(api, source)
    store.offline = True
    session.set_name("Local pending")  # type: ignore[attr-defined]
    queued = session.save()  # type: ignore[attr-defined]
    assert isinstance(queued, Queued)
    store.offline = False
    restarted = create_workbench(
        persistence=store,
        audio_storage=LocalAudioStorage(session.audio.local_path.parents[1]),  # type: ignore[attr-defined]
        recovery_outbox=FileRecoveryOutbox(recovery_root),
    )
    workspace_loads = store.calls.count("load_workspace")

    with pytest.raises(PendingRecoveryOperationError):
        restarted.recordings.open(session.recording_id)  # type: ignore[attr-defined]

    assert store.calls.count("load_workspace") == workspace_loads
    assert isinstance(restarted.recovery.retry(queued.operation_id), RecoveryApplied)
    reopened = restarted.recordings.open(session.recording_id)  # type: ignore[attr-defined]
    assert reopened.name == "Local pending"


def test_retry_all_keeps_children_blocked_by_an_existing_conflict(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
) -> None:
    api, store, source, _ = application_system
    session = create_recording(api, source)
    recording_id = session.recording_id  # type: ignore[attr-defined]
    original_head = session.base_revision_id  # type: ignore[attr-defined]
    store.offline = True
    session.set_name("Queued parent")  # type: ignore[attr-defined]
    parent = session.save()  # type: ignore[attr-defined]
    session.set_name("Queued child")  # type: ignore[attr-defined]
    child = session.save()  # type: ignore[attr-defined]
    assert isinstance(parent, Queued)
    assert isinstance(child, Queued)
    store.offline = False
    store.save_snapshot(
        SaveRecordingSnapshotRequest(
            recording_id=recording_id,
            expected_parent_revision_id=original_head,
            name="Remote winner",
            language="en",
            libraries=(),
            annotations=(),
        )
    )

    first_run = api.recovery.retry_all()  # type: ignore[attr-defined]

    assert len(first_run) == 1
    assert isinstance(first_run[0], RecoveryConflict)
    assert [item.operation_id for item in api.recovery.list_pending()] == [  # type: ignore[attr-defined]
        child.operation_id
    ]
    save_attempts = store.calls.count("save_snapshot")
    assert api.recovery.retry_all() == ()  # type: ignore[attr-defined]
    assert store.calls.count("save_snapshot") == save_attempts


def test_session_reuses_validation_and_annotation_lookup_caches(
    application_system: tuple[object, MemoryPersistenceStore, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, _, source, _ = application_system
    version, pin = publish_test_library(api)
    concept_index_calls = 0
    validator_for_calls = 0
    original_concept_index = validation_module.concept_index
    original_validator_for = validation_module.validator_for

    def counting_concept_index(bindings: object) -> object:
        nonlocal concept_index_calls
        concept_index_calls += 1
        return original_concept_index(bindings)  # type: ignore[arg-type]

    def counting_validator_for(schema: object) -> object:
        nonlocal validator_for_calls
        validator_for_calls += 1
        return original_validator_for(schema)  # type: ignore[arg-type]

    monkeypatch.setattr(validation_module, "concept_index", counting_concept_index)
    monkeypatch.setattr(validation_module, "validator_for", counting_validator_for)
    session = create_recording(api, source, libraries=(pin,))
    concept = ConceptRef("le.test@1.0.0:event")
    first = annotation(concept, start=10, end=20)
    second = annotation(concept, start=30, end=40)

    session.add_annotations((first, second))  # type: ignore[attr-defined]
    assert session.resolve_concept(concept).entry_key == "event"  # type: ignore[attr-defined]
    assert session.get_annotation(second.id) == second  # type: ignore[attr-defined]
    assert concept_index_calls == 1
    assert validator_for_calls == 1
    assert session._state.annotations_by_id == {  # type: ignore[attr-defined]
        first.id: first,
        second.id: second,
    }

    session.remove_annotation(first.id)  # type: ignore[attr-defined]
    session.remove_annotation(second.id)  # type: ignore[attr-defined]
    session.unpin_library_version("le.test", "1.0.0")  # type: ignore[attr-defined]
    session.pin_library_version(version)  # type: ignore[attr-defined]
    assert concept_index_calls == 3
    assert validator_for_calls == 2
