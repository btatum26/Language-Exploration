import hashlib
import json
from uuid import uuid4

import pytest

from application import CreateRecordingCommand
from application.validation import library_content_sha256
from models import ConceptRef, Library, PinnedLibraryVersion, TimeIntervalGeometry
from scripts.migrate_core_placeholder import dump_tables, migrate
from tests.database.test_core_first_use_postgresql import core_store  # noqa: F401
from tests.test_core_library import make_api
from tests.test_core_transition import placeholder_publication
from tests.test_gui import _write_wav

pytestmark = pytest.mark.postgresql


@pytest.mark.parametrize("rollback", [False, True])
def test_owner_transition_preserves_history_and_supports_rollback(
    core_store, tmp_path, rollback,  # noqa: F811
):
    store, factory = core_store
    api = make_api(store, tmp_path)
    library = api.libraries.create(Library(id=uuid4(), namespace="core", name="Core annotations"))
    old = api.libraries.publish_version(placeholder_publication(library.id))
    source = tmp_path / "sample.wav"
    _write_wav(source)
    session = api.import_recording(
        CreateRecordingCommand(
            recording_id=uuid4(),
            source_audio_path=source,
            name="Keep recording",
            language="en",
            libraries=(
                PinnedLibraryVersion(
                    namespace="core", version="0.1", content_sha256=old.content_sha256
                ),
            ),
        )
    )
    annotation = session.create_annotation(
        concept_ref=ConceptRef("core@0.1:test"),
        geometry=TimeIntervalGeometry(start_sample=10, end_sample=60),
        label="Keep label",
        note="Keep note",
    )
    session.save()
    session.set_name("Renamed recording")
    session.save()
    session.close()
    engine = factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"]["registry_align"]
    backup = tmp_path / "before.json"
    with engine.connect() as connection:
        transaction = connection.begin()
        before = dump_tables(connection, schema)
        preflight = migrate(connection, schema=schema)
        assert preflight["annotation_revision_rows"] == 2
        assert dump_tables(connection, schema) == before
        report = migrate(connection, schema=schema, backup_path=backup)
        assert report["status"] == "migrated"
        assert json.loads(backup.read_text("utf-8"))["tables"] == before
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == report["backup_sha256"]
        assert migrate(connection, schema=schema)["status"] == "already_migrated"
        if rollback:
            transaction.rollback()
        else:
            transaction.commit()
    fresh = make_api(store, tmp_path)
    if rollback:
        assert fresh.libraries.get_version(old.id) == old
    else:
        target = fresh.ensure_core_library()
        assert target.id == old.id and target.entries[2].id == old.entries[0].id
        assert target.content_sha256 == library_content_sha256(target)
        reopened = fresh.open_recording(session.recording_id)
        assert reopened.annotations == (
            annotation.model_copy(
                update={
                    "concept_ref": ConceptRef("core@0.1:word"),
                }
            ),
        )
        reopened.close()
