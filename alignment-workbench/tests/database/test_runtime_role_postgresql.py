from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.postgresql

SCHEMA = "registry_align"
RUNTIME_ROLE = "registry_align_app"
TABLES = {
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


@pytest.fixture(scope="module")
def runtime_engine() -> Iterator[Engine]:
    runtime_url = os.getenv("TEST_RUNTIME_DATABASE_URL")
    if not runtime_url:
        pytest.skip("TEST_RUNTIME_DATABASE_URL is required for runtime-role tests")
    url = make_url(runtime_url)
    if url.drivername != "postgresql+psycopg":
        pytest.fail("TEST_RUNTIME_DATABASE_URL must use postgresql+psycopg")

    engine = create_engine(runtime_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            current_user = connection.scalar(text("SELECT current_user"))
            if current_user != RUNTIME_ROLE:
                pytest.fail(
                    "TEST_RUNTIME_DATABASE_URL must authenticate as registry_align_app; "
                    f"connected as {current_user}"
                )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def runtime_connection(runtime_engine: Engine) -> Iterator[Connection]:
    with runtime_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


def _effective_privileges(
    connection: Connection, table_schema: str, privilege_type: str
) -> set[tuple[str, str]]:
    rows = connection.execute(
        text(
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE grantee = current_user AND table_schema = :schema "
            "AND privilege_type = :privilege"
        ),
        {"schema": table_schema, "privilege": privilege_type},
    )
    return {(row.table_name, row.privilege_type) for row in rows}


def _insert_complete_graph(connection: Connection) -> dict[str, UUID]:
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
    token = ids["asset"].hex
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.audio_assets "
            "(id, storage_uri, sha256, sample_rate_hz, frame_count, channels) "
            "VALUES (:asset, :uri, :sha, 16000, 16000, 1)"
        ),
        {
            **ids,
            "uri": f"registry-audio://assets/{ids['asset']}",
            "sha": (token * 2)[:64],
        },
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.speakers (id, external_key, display_name) "
            "VALUES (:speaker, :external_key, 'Runtime Test Speaker')"
        ),
        {**ids, "external_key": f"runtime-{token}"},
    )
    connection.execute(
        text(f"INSERT INTO {SCHEMA}.recordings (id, audio_asset_id) VALUES (:recording, :asset)"),
        ids,
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.annotation_libraries (id, namespace, name) "
            "VALUES (:library, :namespace, 'Runtime Test Library')"
        ),
        {**ids, "namespace": f"runtime.{token}"},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.library_versions "
            "(id, library_id, version_label, content_sha256) "
            "VALUES (:version, :library, '1.0.0', :sha)"
        ),
        {**ids, "sha": (ids["version"].hex * 2)[:64]},
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.library_entries "
            "(id, library_version_id, position, entry_key, display_name, description, "
            "allowed_geometry_types) "
            "VALUES (:entry, :version, 0, 'event', 'Event', '', ARRAY['point']::text[])"
        ),
        ids,
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.recording_revisions "
            "(id, recording_id, revision_number, name, default_speaker_ref, language) "
            "VALUES (:revision, :recording, 1, 'Runtime Test', :speaker, 'it')"
        ),
        ids,
    )
    connection.execute(
        text(
            f"INSERT INTO {SCHEMA}.recording_revision_libraries "
            "(recording_revision_id, library_version_id, position) "
            "VALUES (:revision, :version, 0)"
        ),
        ids,
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
        text(f"UPDATE {SCHEMA}.recordings SET head_revision_id = :revision WHERE id = :recording"),
        ids,
    )
    return ids


def _assert_permission_denied(
    connection: Connection, statement: str, parameters: dict[str, object]
) -> None:
    with pytest.raises(DBAPIError) as captured:
        with connection.begin_nested():
            connection.execute(text(statement), parameters)
    assert getattr(captured.value.orig, "sqlstate", None) == "42501"


def test_runtime_role_has_exact_catalog_privileges(runtime_connection: Connection) -> None:
    assert runtime_connection.scalar(
        text("SELECT has_schema_privilege(current_user, :schema, 'USAGE')"),
        {"schema": SCHEMA},
    )

    for privilege in ("SELECT", "INSERT"):
        assert _effective_privileges(runtime_connection, SCHEMA, privilege) == {
            (table, privilege) for table in TABLES
        }
    assert _effective_privileges(runtime_connection, SCHEMA, "UPDATE") == set()
    assert _effective_privileges(runtime_connection, SCHEMA, "DELETE") == set()
    assert _effective_privileges(runtime_connection, SCHEMA, "TRUNCATE") == set()

    update_columns = {
        (row.table_name, row.column_name)
        for row in runtime_connection.execute(
            text(
                "SELECT table_name, column_name "
                "FROM information_schema.column_privileges "
                "WHERE grantee = current_user AND table_schema = :schema "
                "AND privilege_type = 'UPDATE'"
            ),
            {"schema": SCHEMA},
        )
    }
    assert update_columns == {("recordings", "head_revision_id")}

    version_table = "public.registry_align_snapshot_alembic_version"
    assert runtime_connection.scalar(
        text("SELECT has_table_privilege(current_user, :table, 'SELECT')"),
        {"table": version_table},
    )
    for privilege in ("INSERT", "UPDATE", "DELETE"):
        assert not runtime_connection.scalar(
            text("SELECT has_table_privilege(current_user, :table, :privilege)"),
            {"table": version_table, "privilege": privilege},
        )

    default_table_privileges = set(
        runtime_connection.scalars(
            text(
                "SELECT acl.privilege_type "
                "FROM pg_default_acl defaults "
                "JOIN pg_namespace namespace "
                "ON namespace.oid = defaults.defaclnamespace "
                "CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
                "JOIN pg_roles owner_role ON owner_role.oid = defaults.defaclrole "
                "JOIN pg_roles grantee_role ON grantee_role.oid = acl.grantee "
                "WHERE namespace.nspname = :schema "
                "AND owner_role.rolname = 'registry_align_owner' "
                "AND grantee_role.rolname = current_user "
                "AND defaults.defaclobjtype = 'r'"
            ),
            {"schema": SCHEMA},
        )
    )
    assert default_table_privileges == {"SELECT", "INSERT"}

    default_sequence_privileges = set(
        runtime_connection.scalars(
            text(
                "SELECT acl.privilege_type "
                "FROM pg_default_acl defaults "
                "JOIN pg_namespace namespace "
                "ON namespace.oid = defaults.defaclnamespace "
                "CROSS JOIN LATERAL aclexplode(defaults.defaclacl) acl "
                "JOIN pg_roles owner_role ON owner_role.oid = defaults.defaclrole "
                "JOIN pg_roles grantee_role ON grantee_role.oid = acl.grantee "
                "WHERE namespace.nspname = :schema "
                "AND owner_role.rolname = 'registry_align_owner' "
                "AND grantee_role.rolname = current_user "
                "AND defaults.defaclobjtype = 'S'"
            ),
            {"schema": SCHEMA},
        )
    )
    assert default_sequence_privileges == set()


def test_runtime_role_can_insert_snapshot_and_only_advance_head(
    runtime_connection: Connection,
) -> None:
    ids = _insert_complete_graph(runtime_connection)
    assert (
        runtime_connection.scalar(
            text(f"SELECT head_revision_id FROM {SCHEMA}.recordings WHERE id = :recording"),
            ids,
        )
        == ids["revision"]
    )

    _assert_permission_denied(
        runtime_connection,
        f"UPDATE {SCHEMA}.recordings SET audio_asset_id = :asset WHERE id = :recording",
        ids,
    )
    _assert_permission_denied(
        runtime_connection,
        f"UPDATE {SCHEMA}.recording_revisions SET name = 'Changed' WHERE id = :revision",
        ids,
    )
    _assert_permission_denied(
        runtime_connection,
        f"DELETE FROM {SCHEMA}.annotations "
        "WHERE recording_revision_id = :revision AND annotation_id = :annotation",
        ids,
    )
