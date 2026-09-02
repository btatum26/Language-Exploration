from __future__ import annotations

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from application.errors import (
    AudioAssetNotFoundError,
    ConcurrentRevisionError,
    PersistenceIntegrityError,
    RecordingNotFoundError,
    RevisionNotFoundError,
)
from models import (
    AnnotatedRecordingSnapshot,
    AudioAsset,
    CreateRecordingRequest,
    Library,
    LibraryEntry,
    LibraryVersion,
    PinnedLibraryVersion,
    PointGeometry,
    SaveRecordingSnapshotRequest,
    SignalAnnotation,
    Speaker,
    TimeFrequencyBoxGeometry,
    TimeFrequencyPolygonGeometry,
    TimeIntervalGeometry,
)
from persistence.sqlalchemy.unit_of_work import SqlAlchemyPersistence

pytestmark = pytest.mark.postgresql

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = os.getenv("TEST_DATABASE_SCHEMA", "registry_align_persistence_test")


def _alembic_config() -> Config:
    config = Config(ROOT / "alembic.ini")
    config.attributes["snapshot_schema"] = SCHEMA
    return config


def _drop_disposable_version_table(test_url: str) -> None:
    table_name = (
        "registry_align_snapshot_alembic_version"
        if SCHEMA == "registry_align"
        else f"{SCHEMA}_alembic_version"
    )
    cleanup_engine = create_engine(test_url)
    try:
        quoted_name = cleanup_engine.dialect.identifier_preparer.quote(table_name)
        with cleanup_engine.begin() as connection:
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS public.{quoted_name}")
    finally:
        cleanup_engine.dispose()


@pytest.fixture(scope="module")
def persistence_engine() -> Iterator[Engine]:
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is required for persistence integration tests")
    url = make_url(test_url)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("TEST_DATABASE_URL must use postgresql+psycopg")
    if not (url.database or "").endswith("_test") and not SCHEMA.endswith("_test"):
        pytest.fail("persistence integration tests require a disposable database or schema")

    previous_admin = os.environ.get("REGISTRY_ALIGN_DATABASE_ADMIN_URL")
    os.environ["REGISTRY_ALIGN_DATABASE_ADMIN_URL"] = test_url
    config = _alembic_config()
    try:
        command.upgrade(config, "head")
        engine = create_engine(
            test_url,
            pool_pre_ping=True,
            execution_options={
                "schema_translate_map": {"registry_align": SCHEMA},
            },
        )
        yield engine
        engine.dispose()
        command.downgrade(config, "base")
        _drop_disposable_version_table(test_url)
    finally:
        if previous_admin is None:
            os.environ.pop("REGISTRY_ALIGN_DATABASE_ADMIN_URL", None)
        else:
            os.environ["REGISTRY_ALIGN_DATABASE_ADMIN_URL"] = previous_admin


@dataclass(frozen=True)
class StoreFixture:
    store: SqlAlchemyPersistence
    connection: Connection


@pytest.fixture
def persistence_store(persistence_engine: Engine) -> Iterator[StoreFixture]:
    with persistence_engine.connect() as connection:
        transaction = connection.begin()
        factory = sessionmaker(
            bind=connection,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        yield StoreFixture(SqlAlchemyPersistence(factory), connection)
        transaction.rollback()


def _hash(identifier: UUID) -> str:
    return identifier.hex * 2


def _audio() -> AudioAsset:
    identifier = uuid4()
    return AudioAsset(
        id=identifier,
        sha256=_hash(identifier),
        storage_uri=f"registry-audio://assets/{identifier}",
        logical_path=f"imports/{identifier}.wav",
        media_type="audio/wav",
        original_extension=".wav",
        codec="pcm_s16le",
        sample_rate_hz=16_000,
        frame_count=160_000,
        channels=1,
        source_metadata={"source": "integration", "nested": {"accepted": True}},
    )


def _speaker() -> Speaker:
    identifier = uuid4()
    return Speaker(
        id=identifier,
        external_key=f"speaker-{identifier.hex}",
        display_name=f"Speaker {identifier.hex[:8]}",
        metadata={"dialect": "test"},
    )


def _publish_library(
    store: SqlAlchemyPersistence,
    *,
    namespace: str,
    entry_keys: tuple[str, ...] = ("event",),
) -> tuple[Library, LibraryVersion]:
    library = Library(id=uuid4(), namespace=namespace, name=f"Library {namespace}")
    store.create_library(library)
    version_id = uuid4()
    version = LibraryVersion(
        id=version_id,
        library_id=library.id,
        version_label="1.0.0",
        content_sha256=_hash(version_id),
        created_at=datetime.now(UTC),
        author="integration",
        entries=tuple(
            LibraryEntry(
                id=uuid4(),
                library_version_id=version_id,
                entry_key=entry_key,
                display_name=entry_key.title(),
                description=f"{entry_key} integration entry",
                allowed_geometry_types=(
                    "point",
                    "time_interval",
                    "time_frequency_box",
                    "time_frequency_polygon",
                ),
                attribute_schema={"type": "object"},
                metadata={"position": position},
            )
            for position, entry_key in enumerate(entry_keys)
        ),
    )
    return library, store.publish_library_version(version)


def _complete_recording(
    store: SqlAlchemyPersistence,
) -> tuple[CreateRecordingRequest, AnnotatedRecordingSnapshot, Speaker]:
    speaker = store.create_speaker(_speaker())
    library_a, version_a = _publish_library(
        store,
        namespace=f"le.persistence.{uuid4().hex}",
        entry_keys=("point", "interval", "box", "polygon"),
    )
    library_b, version_b = _publish_library(
        store,
        namespace=f"le.secondary.{uuid4().hex}",
    )
    references = [
        f"{library_a.namespace}@{version_a.version_label}:{entry.entry_key}"
        for entry in version_a.entries
    ]
    annotations = (
        SignalAnnotation(
            id=uuid4(),
            concept_ref=references[0],
            geometry=PointGeometry(start_sample=10),
        ),
        SignalAnnotation(
            id=uuid4(),
            concept_ref=references[1],
            geometry=TimeIntervalGeometry(start_sample=20, end_sample=40),
        ),
        SignalAnnotation(
            id=uuid4(),
            concept_ref=references[2],
            geometry=TimeFrequencyBoxGeometry(
                start_sample=50,
                end_sample=80,
                min_frequency_hz=100,
                max_frequency_hz=500,
            ),
        ),
        SignalAnnotation(
            id=uuid4(),
            concept_ref=references[3],
            geometry=TimeFrequencyPolygonGeometry(
                start_sample=100,
                end_sample=200,
                min_frequency_hz=100,
                max_frequency_hz=500,
                vertices=(
                    {"sample": 100, "frequency_hz": 100},
                    {"sample": 200, "frequency_hz": 100},
                    {"sample": 150, "frequency_hz": 500},
                ),
            ),
            attributes={"nested": {"values": [1, 2, 3]}},
            confidence=0.9,
            note="polygon",
        ),
    )
    request = CreateRecordingRequest(
        recording_id=uuid4(),
        audio_asset=_audio(),
        name="Complete recording",
        default_speaker_ref=speaker.id,
        language="it",
        author="integration",
        message="initial",
        libraries=(
            PinnedLibraryVersion(
                namespace=library_b.namespace,
                version=version_b.version_label,
                content_sha256=version_b.content_sha256,
            ),
            PinnedLibraryVersion(
                namespace=library_a.namespace,
                version=version_a.version_label,
                content_sha256=version_a.content_sha256,
            ),
        ),
        annotations=annotations,
    )
    return request, store.create_recording(request), speaker


def test_audio_speaker_and_ordered_library_round_trips(
    persistence_store: StoreFixture,
) -> None:
    store = persistence_store.store
    audio = _audio()
    speaker = _speaker()

    assert store.register_audio_asset(audio) == audio
    assert store.register_audio_asset(audio) == audio
    assert store.get_audio_asset(audio.id).storage_uri == audio.storage_uri
    assert store.create_speaker(speaker) == speaker
    assert store.get_speaker(speaker.id) == speaker
    assert speaker in store.list_speakers()

    library, version = _publish_library(
        store,
        namespace=f"le.ordered.{uuid4().hex}",
        entry_keys=("third", "first", "second"),
    )
    assert store.get_library(library.id) == library
    loaded = store.get_library_version(version.id)
    assert [entry.entry_key for entry in loaded.entries] == ["third", "first", "second"]
    assert store.list_library_versions(library.id) == (loaded,)


def test_complete_recording_current_snapshot_and_workspace(
    persistence_store: StoreFixture,
) -> None:
    request, created, speaker = _complete_recording(persistence_store.store)
    store = persistence_store.store

    current = store.load_snapshot(request.recording_id)
    workspace = store.load_workspace(request.recording_id)

    assert current == created
    assert isinstance(current, AnnotatedRecordingSnapshot)
    assert not isinstance(current, Session)
    assert not type(workspace).__module__.startswith("persistence.sqlalchemy")
    assert current.audio_asset.storage_uri == request.audio_asset.storage_uri
    assert [pin.namespace for pin in current.libraries] == [
        pin.namespace for pin in request.libraries
    ]
    assert [annotation.id for annotation in current.annotations] == [
        annotation.id for annotation in request.annotations
    ]
    polygon = current.annotations[-1]
    assert isinstance(polygon.geometry, TimeFrequencyPolygonGeometry)
    assert polygon.geometry.vertices == request.annotations[-1].geometry.vertices  # type: ignore[union-attr]
    assert polygon.attributes == request.annotations[-1].attributes
    assert workspace.default_speaker == speaker
    assert [version.content_sha256 for version in workspace.library_versions] == [
        pin.content_sha256 for pin in request.libraries
    ]
    assert all(version.entries for version in workspace.library_versions)
    summary = next(
        item for item in store.list_recordings() if item.recording_id == request.recording_id
    )
    assert summary.audio_storage_uri == request.audio_asset.storage_uri
    assert summary.revision_number == 1


def test_historical_load_append_only_save_and_stale_editor_rejection(
    persistence_store: StoreFixture,
) -> None:
    request, first, _ = _complete_recording(persistence_store.store)
    store = persistence_store.store
    save = SaveRecordingSnapshotRequest(
        recording_id=request.recording_id,
        expected_parent_revision_id=first.revision.id,
        name="Revised recording",
        default_speaker_ref=request.default_speaker_ref,
        language=request.language,
        author="integration",
        message="second",
        libraries=request.libraries,
        annotations=tuple(reversed(request.annotations)),
    )

    second = store.save_snapshot(save)

    assert second.revision.number == 2
    assert second.revision.parent_id == first.revision.id
    assert store.load_snapshot(request.recording_id).revision.id == second.revision.id
    assert store.load_snapshot(request.recording_id, first.revision.id) == first
    history = store.list_recording_revisions(request.recording_id)
    assert [item.metadata.id for item in history] == [first.revision.id, second.revision.id]
    assert [item.annotation_count for item in history] == [4, 4]
    with pytest.raises(ConcurrentRevisionError):
        store.save_snapshot(save)
    assert len(store.list_recording_revisions(request.recording_id)) == 2


def test_missing_recording_and_revision_errors(persistence_store: StoreFixture) -> None:
    store = persistence_store.store
    with pytest.raises(RecordingNotFoundError):
        store.load_snapshot(uuid4())

    request, _, _ = _complete_recording(store)
    with pytest.raises(RevisionNotFoundError):
        store.load_snapshot(request.recording_id, uuid4())


def test_intentional_failure_rolls_back_initial_recording_atomically(
    persistence_store: StoreFixture,
) -> None:
    store = persistence_store.store
    library, version = _publish_library(
        store,
        namespace=f"le.rollback.{uuid4().hex}",
    )
    audio = _audio()
    request = CreateRecordingRequest(
        recording_id=uuid4(),
        audio_asset=audio,
        name="Must roll back",
        default_speaker_ref=uuid4(),
        language="it",
        libraries=(
            PinnedLibraryVersion(
                namespace=library.namespace,
                version=version.version_label,
                content_sha256=version.content_sha256,
            ),
        ),
        annotations=(),
    )

    with pytest.raises(PersistenceIntegrityError):
        store.create_recording(request)
    with pytest.raises(RecordingNotFoundError):
        store.load_snapshot(request.recording_id)
    with pytest.raises(AudioAssetNotFoundError, match="audio asset"):
        store.get_audio_asset(audio.id)


def test_workspace_uses_four_queries_and_has_no_lazy_loads(
    persistence_store: StoreFixture,
) -> None:
    request, _, _ = _complete_recording(persistence_store.store)
    statements: list[str] = []

    def before_cursor_execute(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(persistence_store.connection, "before_cursor_execute", before_cursor_execute)
    try:
        workspace = persistence_store.store.load_workspace(request.recording_id)
        select_statements = [
            statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
        ]
        assert len(select_statements) == 4
        assert workspace.snapshot.annotations[-1].geometry.type == "time_frequency_polygon"
        assert workspace.library_versions[-1].entries[-1].display_name
        assert workspace.default_speaker is not None
        assert (
            len(
                [
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith("SELECT")
                ]
            )
            == 4
        )
    finally:
        event.remove(persistence_store.connection, "before_cursor_execute", before_cursor_execute)


def test_realistic_5800_annotation_workspace_reports_query_and_mapping_time(
    persistence_store: StoreFixture,
) -> None:
    store = persistence_store.store
    library, version = _publish_library(
        store,
        namespace=f"le.performance.{uuid4().hex}",
    )
    reference = f"{library.namespace}@{version.version_label}:event"
    annotations = tuple(
        SignalAnnotation(
            id=uuid4(),
            concept_ref=reference,
            geometry=PointGeometry(start_sample=index * 2),
            attributes={"index": index},
        )
        for index in range(5_800)
    )
    request = CreateRecordingRequest(
        recording_id=uuid4(),
        audio_asset=_audio(),
        name="5,800 annotation fixture",
        language="it",
        libraries=(
            PinnedLibraryVersion(
                namespace=library.namespace,
                version=version.version_label,
                content_sha256=version.content_sha256,
            ),
        ),
        annotations=annotations,
    )
    store.create_recording(request)

    query_started: dict[int, float] = {}
    query_durations: list[float] = []

    def before_cursor_execute(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            query_started[id(context)] = time.perf_counter()

    def after_cursor_execute(
        _connection: Connection,
        _cursor: object,
        _statement: str,
        _parameters: object,
        context: object,
        _executemany: bool,
    ) -> None:
        started_at = query_started.pop(id(context), None)
        if started_at is not None:
            query_durations.append(time.perf_counter() - started_at)

    event.listen(persistence_store.connection, "before_cursor_execute", before_cursor_execute)
    event.listen(persistence_store.connection, "after_cursor_execute", after_cursor_execute)
    try:
        started = time.perf_counter()
        workspace = store.load_workspace(request.recording_id)
        elapsed = time.perf_counter() - started
    finally:
        event.remove(persistence_store.connection, "before_cursor_execute", before_cursor_execute)
        event.remove(persistence_store.connection, "after_cursor_execute", after_cursor_execute)

    approximate_mapping = max(0.0, elapsed - sum(query_durations))
    print(
        "PERFORMANCE "
        f"annotations={len(workspace.snapshot.annotations)} "
        f"queries={len(query_durations)} "
        f"load_seconds={elapsed:.6f} "
        f"mapping_seconds={approximate_mapping:.6f}"
    )
    assert len(workspace.snapshot.annotations) == 5_800
    assert len(query_durations) == 4
