"""Discovery metadata persists in disposable PostgreSQL immutable revisions."""

from uuid import uuid4

import pytest

from application import CreateRecordingCommand, Saved
from models import RecordingMetadata
from tests.database.test_core_first_use_postgresql import core_store  # noqa: F401
from tests.test_application_api import write_wav
from tests.test_core_library import make_api

pytestmark = pytest.mark.postgresql


def test_discovery_revision_round_trip(core_store, tmp_path):  # noqa: F811
    store, _ = core_store
    api = make_api(store, tmp_path)
    source = tmp_path / "metadata.wav"
    write_wav(source)
    initial = RecordingMetadata(
        languages=("it", "en"),
        speakers=("Ben", "Luca"),
        collections=("Session 1", "Corpus"),
        tags=("story",),
    )
    session = api.import_recording(
        CreateRecordingCommand(
            recording_id=uuid4(),
            source_audio_path=source,
            name="Metadata",
            language="und",
            metadata=initial,
        )
    )
    original = store.load_snapshot(session.recording_id)
    changed = RecordingMetadata(speakers=("Miri",), tags=("updated",))
    session.set_metadata(changed)
    saved = session.save()
    assert isinstance(saved, Saved)
    assert store.load_snapshot(session.recording_id).metadata == changed
    assert store.load_snapshot(session.recording_id, original.revision.id) == original
    assert saved.snapshot.audio_asset == original.audio_asset
    assert saved.snapshot.annotations == original.annotations
    record = api.list_recordings()[0]
    assert record.metadata == changed and record.added_at is not None
    session.close()
