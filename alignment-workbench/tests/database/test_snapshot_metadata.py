from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from persistence.sqlalchemy import Base, build_engine, build_session_factory

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


def test_metadata_contains_only_the_snapshot_tables_in_registry_schema() -> None:
    assert {table.name for table in Base.metadata.tables.values()} == EXPECTED_TABLES
    assert {table.schema for table in Base.metadata.tables.values()} == {"registry_align"}


def test_metadata_has_named_constraints_and_indexes() -> None:
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            assert constraint.name is not None
        for index in table.indexes:
            assert index.name is not None

    annotations = Base.metadata.tables["registry_align.annotations"]
    annotation_fks = {
        tuple(column.name for column in constraint.columns)
        for constraint in annotations.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert annotation_fks == {
        ("recording_revision_id", "library_version_id"),
        ("library_entry_id", "library_version_id"),
    }
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_annotations_geometry_shape"
        for constraint in annotations.constraints
    )


def test_expected_uniques_and_indexes_are_declared() -> None:
    audio_assets = Base.metadata.tables["registry_align.audio_assets"]
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_audio_assets_storage_uri_nonempty"
        for constraint in audio_assets.constraints
    )

    entries = Base.metadata.tables["registry_align.library_entries"]
    entry_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in entries.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("library_version_id", "entry_key") in entry_uniques
    assert ("library_version_id", "position") in entry_uniques
    assert ("id", "library_version_id") in entry_uniques

    revisions = Base.metadata.tables["registry_align.recording_revisions"]
    assert any(
        isinstance(index, Index)
        and index.name == "uq_recording_revisions_parent_revision_id_not_null"
        and index.unique
        for index in revisions.indexes
    )


def test_engine_and_session_factory_follow_runtime_contract() -> None:
    engine = build_engine("postgresql+psycopg://user:secret@127.0.0.1:5433/example")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.pool.size() == 2
        factory = build_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False
        assert factory.kw["autoflush"] is False
    finally:
        engine.dispose()
