from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from alembic import command

pytestmark = pytest.mark.postgresql

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = os.getenv("TEST_DATABASE_SCHEMA", "registry_align")
EXPECTED_TABLES = {
    "annotation_libraries",
    "annotations",
    "audio_assets",
    "library_entries",
    "library_versions",
    "recording_revision_libraries",
    "recording_revisions",
    "recordings",
    "speakers",
}


def _alembic_config() -> Config:
    config = Config(ROOT / "alembic.ini")
    config.attributes["snapshot_schema"] = SCHEMA
    config.attributes["manage_ssh_tunnel"] = False
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
def database_engine(database_tunnel: None) -> Iterator[Engine]:
    test_url = os.getenv("TEST_DATABASE_URL")
    if not test_url:
        pytest.skip("TEST_DATABASE_URL is required for snapshot database integration tests")
    url = make_url(test_url)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("TEST_DATABASE_URL must use postgresql+psycopg")
    if not (url.database or "").endswith("_test") and not SCHEMA.endswith("_test"):
        pytest.fail(
            "TEST_DATABASE_URL must name a database ending in _test, or "
            "TEST_DATABASE_SCHEMA must name an isolated schema ending in _test"
        )

    previous_admin = os.environ.get("REGISTRY_ALIGN_DATABASE_ADMIN_URL")
    os.environ["REGISTRY_ALIGN_DATABASE_ADMIN_URL"] = test_url
    config = _alembic_config()
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        engine = create_engine(test_url, pool_pre_ping=True)
        yield engine
        engine.dispose()
        command.downgrade(config, "base")
        _drop_disposable_version_table(test_url)
    finally:
        if previous_admin is None:
            os.environ.pop("REGISTRY_ALIGN_DATABASE_ADMIN_URL", None)
        else:
            os.environ["REGISTRY_ALIGN_DATABASE_ADMIN_URL"] = previous_admin


@pytest.fixture
def connection(database_engine: Engine) -> Iterator[Connection]:
    with database_engine.connect() as database_connection:
        transaction = database_connection.begin()
        yield database_connection
        transaction.rollback()


def _insert_graph(connection: Connection) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in (
            "asset",
            "speaker",
            "recording",
            "revision",
            "library",
            "version",
            "entry",
            "annotation",
        )
    }
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.audio_assets "
            "(id, storage_uri, sha256, sample_rate_hz, frame_count, channels) "
            "VALUES (:id, :uri, :sha, 16000, 16000, 1)"
        ),
        {
            "id": ids["asset"],
            "uri": f"registry-audio://assets/{ids['asset']}",
            "sha": "a" * 64,
        },
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.speakers (id, external_key, display_name) "
            "VALUES (:id, 'speaker-one', 'Speaker One')"
        ),
        {"id": ids["speaker"]},
    )
    connection.execute(
        text(f"INSERT INTO {SCHEMA}.recordings (id, audio_asset_id) VALUES (:id, :asset_id)"),
        {"id": ids["recording"], "asset_id": ids["asset"]},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.annotation_libraries (id, namespace, name) "
            "VALUES (:id, 'le.test', 'Test library')"
        ),
        {"id": ids["library"]},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.library_versions "
            "(id, library_id, version_label, content_sha256) "
            "VALUES (:id, :library_id, '1.0.0', :sha)"
        ),
        {"id": ids["version"], "library_id": ids["library"], "sha": "b" * 64},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.library_entries "
            "(id, library_version_id, position, entry_key, display_name, description, "
            "allowed_geometry_types) "
            "VALUES (:id, :version_id, 0, 'event', 'Event', '', ARRAY['point']::text[])"
        ),
        {"id": ids["entry"], "version_id": ids["version"]},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.recording_revisions "
            "(id, recording_id, revision_number, name, default_speaker_ref, language) "
            "VALUES (:id, :recording_id, 1, 'Recording', :speaker_id, 'it')"
        ),
        {
            "id": ids["revision"],
            "recording_id": ids["recording"],
            "speaker_id": ids["speaker"],
        },
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.recording_revision_libraries "
            "(recording_revision_id, library_version_id, position) VALUES (:revision, :version, 0)"
        ),
        {"revision": ids["revision"], "version": ids["version"]},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.annotations "
            "(recording_revision_id, annotation_id, position, library_version_id, "
            "library_entry_id, geometry_type, start_sample) "
            "VALUES (:revision, :annotation, 0, :version, :entry, 'point', 100)"
        ),
        ids,
    )
    connection.execute(
        text(f"UPDATE {SCHEMA}.recordings SET head_revision_id=:revision WHERE id=:recording"),
        ids,
    )
    return ids


def _assert_rejected(connection: Connection, statement: str, parameters: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(text(statement), parameters)


def test_migration_catalog_shape(database_engine: Engine) -> None:
    inspector = inspect(database_engine)
    assert set(inspector.get_table_names(schema=SCHEMA)) == EXPECTED_TABLES
    version_table = (
        "registry_align_snapshot_alembic_version"
        if SCHEMA == "registry_align"
        else f"{SCHEMA}_alembic_version"
    )
    assert inspector.has_table(version_table, schema="public")
    with database_engine.connect() as connection:
        assert (
            connection.scalar(text(f"SELECT version_num FROM public.{version_table}"))
            == "20260830_0002_storage_uri"
        )


def test_minimal_graph_and_uniqueness_constraints(connection: Connection) -> None:
    ids = _insert_graph(connection)
    assert (
        connection.scalar(
            text(f"SELECT head_revision_id FROM {SCHEMA}.recordings WHERE id=:recording"), ids
        )
        == ids["revision"]
    )

    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.audio_assets "
        "(storage_uri, sha256, sample_rate_hz, frame_count, channels) "
        "VALUES ('registry-audio://assets/duplicate-bytes', :sha, 1, 1, 1)",
        {"sha": "a" * 64},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.annotation_libraries (namespace, name) "
        "VALUES ('le.test', 'Duplicate')",
        {},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.library_entries "
        "(library_version_id, position, entry_key, display_name, description, "
        "allowed_geometry_types) VALUES (:version, 1, 'event', 'Duplicate', '', ARRAY['point'])",
        ids,
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.recording_revisions "
        "(recording_id, revision_number, name, language) VALUES (:recording, 1, 'Duplicate', 'it')",
        ids,
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.annotations "
        "(recording_revision_id, annotation_id, position, library_version_id, "
        "library_entry_id, geometry_type, start_sample) "
        "VALUES (:revision, gen_random_uuid(), 0, :version, :entry, 'point', 0)",
        ids,
    )


def test_same_recording_parent_head_and_linear_history_constraints(connection: Connection) -> None:
    ids = _insert_graph(connection)
    other_recording = uuid4()
    other_revision = uuid4()
    connection.execute(
        text(f"INSERT INTO {SCHEMA}.recordings (id, audio_asset_id) VALUES (:id, :asset)"),
        {"id": other_recording, "asset": ids["asset"]},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.recording_revisions "
            "(id, recording_id, revision_number, name, language) "
            "VALUES (:id, :recording, 1, 'Other', 'it')"
        ),
        {"id": other_revision, "recording": other_recording},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.recording_revisions "
        "(recording_id, revision_number, parent_revision_id, name, language) "
        "VALUES (:recording, 2, :other_revision, 'Invalid parent', 'it')",
        {**ids, "other_revision": other_revision},
    )
    child = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.recording_revisions "
            "(id, recording_id, revision_number, parent_revision_id, name, language) "
            "VALUES (:id, :recording, 2, :revision, 'Second', 'it')"
        ),
        {**ids, "id": child},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.recording_revisions "
        "(recording_id, revision_number, parent_revision_id, name, language) "
        "VALUES (:recording, 3, :revision, 'Branch', 'it')",
        ids,
    )
    _assert_rejected(
        connection,
        f"UPDATE {SCHEMA}.recordings SET head_revision_id=:other_revision WHERE id=:recording",
        {**ids, "other_revision": other_revision},
    )


def test_annotation_pin_entry_geometry_and_json_constraints(connection: Connection) -> None:
    ids = _insert_graph(connection)
    second_version = uuid4()
    second_entry = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.library_versions "
            "(id, library_id, version_label, content_sha256) "
            "VALUES (:id, :library, '2.0.0', :sha)"
        ),
        {"id": second_version, "library": ids["library"], "sha": "c" * 64},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.library_entries "
            "(id, library_version_id, position, entry_key, display_name, description, "
            "allowed_geometry_types) "
            "VALUES (:id, :version, 0, 'event', 'Event', '', ARRAY['point'])"
        ),
        {"id": second_entry, "version": second_version},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.annotations "
        "(recording_revision_id, annotation_id, position, library_version_id, "
        "library_entry_id, geometry_type, start_sample) "
        "VALUES (:revision, gen_random_uuid(), 1, :version, :entry, 'point', 0)",
        {**ids, "version": second_version, "entry": second_entry},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.recording_revision_libraries "
            "(recording_revision_id, library_version_id, position) VALUES (:revision, :version, 1)"
        ),
        {**ids, "version": second_version},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.annotations "
        "(recording_revision_id, annotation_id, position, library_version_id, "
        "library_entry_id, geometry_type, start_sample) "
        "VALUES (:revision, gen_random_uuid(), 1, :version, :entry, 'point', 0)",
        {**ids, "version": second_version},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.annotations "
        "(recording_revision_id, annotation_id, position, library_version_id, "
        "library_entry_id, geometry_type, start_sample, end_sample) "
        "VALUES (:revision, gen_random_uuid(), 1, :version, :entry, 'point', 0, 1)",
        ids,
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.annotations "
        "(recording_revision_id, annotation_id, position, library_version_id, "
        "library_entry_id, geometry_type, start_sample, confidence) "
        "VALUES (:revision, gen_random_uuid(), 1, :version, :entry, 'point', 0, 2)",
        ids,
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.audio_assets "
        "(storage_uri, sha256, sample_rate_hz, frame_count, channels, source_metadata) "
        "VALUES ('registry-audio://assets/invalid-json', :sha, 1, 1, 1, '[]'::jsonb)",
        {"sha": "d" * 64},
    )


def test_storage_uri_must_not_be_empty(connection: Connection) -> None:
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.audio_assets "
        "(storage_uri, sha256, sample_rate_hz, frame_count, channels) "
        "VALUES ('', :sha, 1, 1, 1)",
        {"sha": "e" * 64},
    )


@pytest.mark.parametrize(
    "geometry_array",
    (
        "ARRAY[]::text[]",
        "ARRAY['unknown']::text[]",
        "ARRAY['point', NULL]::text[]",
    ),
    ids=("empty", "unknown", "null-item"),
)
def test_allowed_geometry_types_reject_invalid_arrays(
    connection: Connection, geometry_array: str
) -> None:
    ids = _insert_graph(connection)
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.library_entries "
        "(library_version_id, position, entry_key, display_name, description, "
        "allowed_geometry_types) "
        f"VALUES (:version, 1, 'invalid', 'Invalid', '', {geometry_array})",
        ids,
    )


def test_default_speaker_ref_must_reference_a_speaker(connection: Connection) -> None:
    ids = _insert_graph(connection)
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.recording_revisions "
        "(recording_id, revision_number, parent_revision_id, name, "
        "default_speaker_ref, language) "
        "VALUES (:recording, 2, :revision, 'Invalid speaker', :speaker, 'it')",
        {**ids, "speaker": uuid4()},
    )


def test_library_version_label_and_content_hash_are_unique_per_library(
    connection: Connection,
) -> None:
    ids = _insert_graph(connection)
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.library_versions "
        "(library_id, version_label, content_sha256) "
        "VALUES (:library, '1.0.0', :sha)",
        {**ids, "sha": "c" * 64},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.library_versions "
        "(library_id, version_label, content_sha256) "
        "VALUES (:library, '2.0.0', :sha)",
        {**ids, "sha": "b" * 64},
    )


def test_pinned_library_position_is_unique_per_revision(connection: Connection) -> None:
    ids = _insert_graph(connection)
    second_version = uuid4()
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.library_versions "
            "(id, library_id, version_label, content_sha256) "
            "VALUES (:id, :library, '2.0.0', :sha)"
        ),
        {"id": second_version, "library": ids["library"], "sha": "c" * 64},
    )
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.recording_revision_libraries "
        "(recording_revision_id, library_version_id, position) "
        "VALUES (:revision, :version, 0)",
        {**ids, "version": second_version},
    )


@pytest.mark.parametrize(
    "invalid_frequency",
    ("'NaN'", "'Infinity'", "'-Infinity'"),
    ids=("nan", "positive-infinity", "negative-infinity"),
)
@pytest.mark.parametrize("column", ("min_frequency_hz", "max_frequency_hz"))
def test_frequency_values_must_be_finite(
    connection: Connection, column: str, invalid_frequency: str
) -> None:
    ids = _insert_graph(connection)
    minimum = f"{invalid_frequency}::double precision" if column == "min_frequency_hz" else "10"
    maximum = f"{invalid_frequency}::double precision" if column == "max_frequency_hz" else "20"
    _assert_rejected(
        connection,
        f"INSERT INTO {SCHEMA}.annotations "
        "(recording_revision_id, annotation_id, position, library_version_id, "
        "library_entry_id, geometry_type, start_sample, end_sample, "
        "min_frequency_hz, max_frequency_hz) "
        f"VALUES (:revision, gen_random_uuid(), 1, :version, :entry, "
        f"'time_frequency_box', 0, 1, {minimum}, {maximum})",
        ids,
    )
