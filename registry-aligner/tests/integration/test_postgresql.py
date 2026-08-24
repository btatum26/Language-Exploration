from __future__ import annotations

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, func, make_url, select, text, update

from registry_align.audio.probe import sha256_file
from registry_align.config import AppConfig, database_admin_url, database_url, load_config
from registry_align.domain.audio import PreparedRecording
from registry_align.domain.entries import RegistryEntry
from registry_align.domain.runs import ProcessRequest
from registry_align.domain.segments import AlignmentSegment, SegmentRevision
from registry_align.pipeline.cache import content_fingerprint
from registry_align.pipeline.service import AlignmentService
from registry_align.storage.postgres import PostgresUnitOfWorkFactory
from registry_align.storage.tables import (
    audio_assets,
    metadata,
    recording_versions,
    recordings,
    runs,
    segment_revisions,
    segments,
)
from registry_align.storage.tunnel import SshTunnel
from registry_align.text.normalizer import normalize_transcript

pytestmark = pytest.mark.postgresql


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False)
    config = load_config(root / "registry-align.example.toml")
    test_url = os.getenv("TEST_DATABASE_URL")
    if test_url and not test_url.startswith("postgresql+psycopg://"):
        pytest.fail("TEST_DATABASE_URL must use postgresql+psycopg")
    with SshTunnel(config.database.ssh_tunnel):
        if test_url:
            database = create_engine(test_url, pool_pre_ping=True)
            metadata.drop_all(database, checkfirst=True)
            metadata.create_all(database)
            try:
                yield database
            finally:
                metadata.drop_all(database, checkfirst=True)
                database.dispose()
            return

        schema = f"registry_align_test_{uuid4().hex}"
        app_url = database_url()
        app_user = make_url(app_url).username
        if not app_user:
            pytest.fail("REGISTRY_ALIGN_DATABASE_URL has no user")
        admin = create_engine(database_admin_url(), pool_pre_ping=True)
        quote = admin.dialect.identifier_preparer.quote
        with admin.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {quote(schema)}"))
            connection.execute(
                text(f"GRANT USAGE, CREATE ON SCHEMA {quote(schema)} TO {quote(app_user)}")
            )
        database = create_engine(
            app_url,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={schema}"},
        )
        metadata.create_all(database)
        try:
            yield database
        finally:
            database.dispose()
            with admin.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {quote(schema)} CASCADE"))
            admin.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    with engine.begin() as connection:
        names = ", ".join(f'"{table.name}"' for table in metadata.tables.values())
        connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


def entry(
    tmp_path: Path, recording_id: str, audio: bytes, transcript: str = "ciao"
) -> RegistryEntry:
    path = tmp_path / recording_id / "audio.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(audio)
    return RegistryEntry(
        id=recording_id,
        source_entry_index=0,
        source_registry_path=tmp_path / "registry.json",
        audio_relative_path=f"{recording_id}/audio.wav",
        audio_resolved_path=path,
        transcript_raw=transcript,
        language="it",
        speaker_id="speaker",
        metadata={"lesson": 1},
    )


def prepared(item: RegistryEntry, source_hash: str | None = None) -> PreparedRecording:
    digest = source_hash or sha256_file(item.audio_resolved_path)
    return PreparedRecording(
        recording_id=item.id,
        source_audio_sha256=digest,
        source_codec="pcm_s16le",
        source_sample_rate_hz=16000,
        source_channels=1,
        source_duration_s=1.0,
        source_frame_count=16000,
        canonical_pcm_path=item.audio_resolved_path,
        canonical_pcm_sha256=digest,
        canonical_sample_rate_hz=16000,
        canonical_channels=1,
        canonical_frame_count=16000,
        alignment_audio_path=item.audio_resolved_path,
        alignment_audio_sha256=digest,
        alignment_sample_rate_hz=16000,
        alignment_channels=1,
        decoder_name="fixture",
        decoder_version="1",
        preparation_fingerprint="audio-profile",
    )


def ingest(
    engine: Engine,
    item: RegistryEntry,
    *,
    overwrite: bool,
    source_hash: str | None = None,
) -> tuple[UUID, str]:
    audio = prepared(item, source_hash)
    transcript = normalize_transcript(item.id, item.transcript_raw)
    fingerprint = content_fingerprint(item, audio.source_audio_sha256, transcript)
    with PostgresUnitOfWorkFactory(engine)() as uow:
        result = uow.repository.ingest_version(
            item,
            audio,
            transcript,
            remote_storage_key=f"{audio.source_audio_sha256[:2]}/{audio.source_audio_sha256}",
            corpus_id="tests",
            content_fingerprint=fingerprint,
            ingestion_reason="test",
            overwrite_existing=overwrite,
        )
        uow.commit()
    return result.recording_version_id, result.state


def provenance() -> dict[str, str]:
    return {
        "backend_name": "fake",
        "backend_version": "1",
        "acoustic_model": "model",
        "dictionary_identity": "dictionary",
        "normalizer_identity": "conservative:1",
        "audio_profile": "audio-profile",
        "pipeline_version": "1",
    }


def claim(engine: Engine, version_id: UUID, fingerprint: str = "a" * 64) -> tuple[UUID, str]:
    with PostgresUnitOfWorkFactory(engine)() as uow:
        result = uow.repository.claim_alignment(
            version_id,
            fingerprint,
            provenance(),
            lease_owner="test-worker",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        uow.commit()
    return result.alignment_result_id, result.state


def model_segment(recording_id: str) -> AlignmentSegment:
    return AlignmentSegment(
        segment_id=f"{recording_id}:word:0",
        recording_id=recording_id,
        tier="word",
        label="ciao",
        backend_label="ciao",
        start_s=0.1,
        end_s=0.9,
        start_sample=1600,
        end_sample=14400,
        timebase_sample_rate_hz=16000,
        source="fake",
        run_id=str(uuid4()),
        model_id="model",
    )


def test_ingestion_skip_overwrite_and_content_versioning(engine: Engine, tmp_path: Path) -> None:
    first_entry = entry(tmp_path, "one", b"audio-one")
    first_id, first_state = ingest(engine, first_entry, overwrite=False)
    skipped_id, skipped_state = ingest(engine, first_entry, overwrite=False)
    unchanged_id, unchanged_state = ingest(engine, first_entry, overwrite=True)

    first_entry.audio_resolved_path.write_bytes(b"audio-two")
    changed_id, changed_state = ingest(engine, first_entry, overwrite=True)

    assert first_state == "created"
    assert skipped_state == "skipped"
    assert unchanged_state == "unchanged"
    assert skipped_id == unchanged_id == first_id
    assert changed_state == "created"
    assert changed_id != first_id
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(recordings)) == 1
        assert connection.scalar(select(func.count()).select_from(recording_versions)) == 2
        assert connection.scalar(select(func.count()).select_from(audio_assets)) == 2


def test_plan_reports_existing_without_stale_new_count(engine: Engine, tmp_path: Path) -> None:
    first = entry(tmp_path, "one", b"one")
    ingest(engine, first, overwrite=False)
    second = entry(tmp_path, "two", b"two")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "entries": [
                    {
                        "id": item.id,
                        "audio_path": item.audio_relative_path,
                        "transcript": item.transcript_raw,
                        "language": item.language,
                    }
                    for item in (first, second)
                ],
            }
        ),
        encoding="utf-8",
    )
    service = AlignmentService(
        AppConfig(),
        uow_factory=PostgresUnitOfWorkFactory(engine),
        adapter_mode=False,
    )

    result = service.plan(registry)

    assert [item.status for item in result.items] == ["existing", "new"]
    assert result.counts["existing"] == 1
    assert result.counts["new"] == 1


def test_all_existing_run_skips_backend_and_batches_run_metadata(
    engine: Engine, tmp_path: Path
) -> None:
    first = entry(tmp_path, "one", b"one")
    ingest(engine, first, overwrite=False)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "entries": [
                    {
                        "id": first.id,
                        "audio_path": first.audio_relative_path,
                        "transcript": first.transcript_raw,
                        "language": first.language,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class BackendMustNotRun:
        name = "unexpected"

        def doctor(self) -> None:
            raise AssertionError("backend doctor must not run for an all-existing corpus")

    service = AlignmentService(
        AppConfig(),
        backend=BackendMustNotRun(),  # type: ignore[arg-type]
        uow_factory=PostgresUnitOfWorkFactory(engine),
        adapter_mode=False,
    )

    result = service.process(ProcessRequest(registry_path=registry))

    assert result.status == "complete"
    assert result.counts == {"skipped": 1}
    with engine.connect() as connection:
        run = connection.execute(select(runs).order_by(runs.c.started_at.desc())).mappings().first()
    assert run is not None
    assert run["backend_summary"]["version"] == "not-invoked"


def test_transcript_change_reuses_audio_and_identical_audio_deduplicates(
    engine: Engine, tmp_path: Path
) -> None:
    first = entry(tmp_path, "one", b"shared", "ciao")
    first_id, _ = ingest(engine, first, overwrite=False)
    changed_text = first.model_copy(update={"transcript_raw": "ciao mondo"})
    second_id, state = ingest(engine, changed_text, overwrite=True)
    other = entry(tmp_path, "two", b"shared", "altro")
    ingest(engine, other, overwrite=False)

    assert state == "created"
    assert second_id != first_id
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(recordings)) == 2
        assert connection.scalar(select(func.count()).select_from(recording_versions)) == 3
        assert connection.scalar(select(func.count()).select_from(audio_assets)) == 1
        asset = connection.execute(select(audio_assets)).mappings().one()
        assert asset["remote_storage_key"] == (f"{asset['sha256'][:2]}/{asset['sha256']}")
    with PostgresUnitOfWorkFactory(engine)() as uow:
        first_asset = uow.repository.current_audio_asset("one")
        second_asset = uow.repository.current_audio_asset("two")
    assert first_asset is not None
    assert second_asset is not None
    assert first_asset["id"] == second_asset["id"]


def test_path_change_does_not_create_a_content_version(engine: Engine, tmp_path: Path) -> None:
    first = entry(tmp_path, "one", b"same")
    version_id, _ = ingest(engine, first, overwrite=False)
    moved_path = tmp_path / "moved" / "renamed.wav"
    moved_path.parent.mkdir()
    moved_path.write_bytes(b"same")
    moved = first.model_copy(
        update={"audio_relative_path": "moved/renamed.wav", "audio_resolved_path": moved_path}
    )

    repeated_id, state = ingest(engine, moved, overwrite=True)

    assert state == "unchanged"
    assert repeated_id == version_id


def test_concurrent_overwrites_cannot_duplicate_versions(engine: Engine, tmp_path: Path) -> None:
    original = entry(tmp_path, "one", b"original")
    ingest(engine, original, overwrite=False)
    original.audio_resolved_path.write_bytes(b"changed")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: ingest(engine, original, overwrite=True), range(2)))

    assert {result[1] for result in results} == {"created", "unchanged"}
    assert results[0][0] == results[1][0]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(recording_versions)) == 2


def test_processing_fingerprint_reuse_and_change(engine: Engine, tmp_path: Path) -> None:
    version_id, _ = ingest(engine, entry(tmp_path, "one", b"audio"), overwrite=False)
    alignment_id, state = claim(engine, version_id)
    assert state == "claimed"
    with PostgresUnitOfWorkFactory(engine)() as uow:
        uow.repository.complete_alignment(alignment_id, (model_segment("one"),), {})
        uow.commit()

    reused_id, reused_state = claim(engine, version_id)
    changed_id, changed_state = claim(engine, version_id, "b" * 64)

    assert (reused_id, reused_state) == (alignment_id, "complete")
    assert changed_state == "claimed"
    assert changed_id != alignment_id


def test_accepted_revision_is_effective_and_immutable(engine: Engine, tmp_path: Path) -> None:
    version_id, _ = ingest(engine, entry(tmp_path, "one", b"audio"), overwrite=False)
    alignment_id, _ = claim(engine, version_id)
    with PostgresUnitOfWorkFactory(engine)() as uow:
        uow.repository.complete_alignment(alignment_id, (model_segment("one"),), {})
        uow.commit()
    with engine.connect() as connection:
        segment_id = connection.scalar(select(segments.c.id))
    assert segment_id is not None
    revision = SegmentRevision(
        revision_id=str(uuid4()),
        segment_id=str(segment_id),
        base_run_id=str(uuid4()),
        start_sample_override=1700,
        label_override="salve",
        review_status="accepted",
        created_at=datetime.now(UTC),
    )
    with PostgresUnitOfWorkFactory(engine)() as uow:
        uow.repository.save_revision(revision)
        effective = uow.repository.effective_segments()
        uow.commit()

    assert effective[0]["model_start_sample"] == 1600
    assert effective[0]["start_sample"] == 1700
    assert effective[0]["label"] == "salve"
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(segment_revisions)) == 1


def test_run_pruning_keeps_durable_history(engine: Engine, tmp_path: Path) -> None:
    ingest(engine, entry(tmp_path, "one", b"audio"), overwrite=False)
    factory = PostgresUnitOfWorkFactory(engine)
    run_ids: list[UUID] = []
    for _ in range(3):
        with factory() as uow:
            run_id = uow.repository.start_run("a" * 64, {}, None)
            uow.repository.finish_run(run_id, "complete", {}, [])
            uow.commit()
            run_ids.append(run_id)
    with engine.begin() as connection:
        connection.execute(
            update(runs)
            .where(runs.c.id.in_(run_ids[:2]))
            .values(started_at=datetime.now(UTC) - timedelta(days=90))
        )
    with factory() as uow:
        candidates = uow.repository.prune_runs(
            now=datetime.now(UTC),
            run_metadata_days=30,
            failed_run_days=14,
            minimum_runs_to_keep=1,
            dry_run=True,
        )
        uow.rollback()
    assert set(candidates) == set(run_ids[:2])
    with factory() as uow:
        deleted = uow.repository.prune_runs(
            now=datetime.now(UTC),
            run_metadata_days=30,
            failed_run_days=14,
            minimum_runs_to_keep=1,
            dry_run=False,
        )
        uow.commit()
    assert set(deleted) == set(run_ids[:2])
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(runs)) == 1
        assert connection.scalar(select(func.count()).select_from(recordings)) == 1
        assert connection.scalar(select(func.count()).select_from(recording_versions)) == 1


def test_transaction_failure_rolls_back_partial_ingestion(engine: Engine, tmp_path: Path) -> None:
    item = entry(tmp_path, "one", b"audio")
    audio = prepared(item)
    transcript = normalize_transcript(item.id, item.transcript_raw)
    with pytest.raises(RuntimeError, match="connection lost"):
        with PostgresUnitOfWorkFactory(engine)() as uow:
            uow.repository.ingest_version(
                item,
                audio,
                transcript,
                remote_storage_key=(f"{audio.source_audio_sha256[:2]}/{audio.source_audio_sha256}"),
                corpus_id="tests",
                content_fingerprint=content_fingerprint(
                    item, audio.source_audio_sha256, transcript
                ),
                ingestion_reason="test",
                overwrite_existing=False,
            )
            raise RuntimeError("connection lost")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(recordings)) == 0
        assert connection.scalar(select(func.count()).select_from(audio_assets)) == 0
        assert connection.scalar(select(func.count()).select_from(recording_versions)) == 0
