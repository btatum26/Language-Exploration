"""Create the immutable annotated-recording snapshot schema.

Revision ID: 20260830_0001_snapshot
Revises: None
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError

from alembic import op

revision = "20260830_0001_snapshot"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "registry_align"


def _configured_schema() -> str:
    value = op.get_context().config.attributes.get("snapshot_schema", SCHEMA)
    if not isinstance(value, str) or not value.replace("_", "").isalnum():
        raise RuntimeError(
            "snapshot migration schema must contain only letters, digits, underscores"
        )
    return value


def upgrade() -> None:
    global SCHEMA
    SCHEMA = _configured_schema()
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "pgcrypto is required for gen_random_uuid(); ask a PostgreSQL administrator "
            "to install it before rerunning the migration"
        ) from exc
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    op.create_table(
        "audio_assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("logical_path", sa.Text()),
        sa.Column("media_type", sa.Text()),
        sa.Column("original_extension", sa.Text()),
        sa.Column("codec", sa.Text()),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("frame_count", sa.BigInteger(), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column(
            "source_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_lower_hex"),
        sa.CheckConstraint("sample_rate_hz > 0", name="sample_rate_positive"),
        sa.CheckConstraint("frame_count > 0", name="frame_count_positive"),
        sa.CheckConstraint("channels > 0", name="channels_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="source_metadata_object",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audio_assets"),
        sa.UniqueConstraint("storage_uri", name="uq_audio_assets_storage_uri"),
        sa.UniqueConstraint("sha256", name="uq_audio_assets_sha256"),
        schema=SCHEMA,
    )

    op.create_table(
        "speakers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("external_key", sa.Text()),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(display_name) > 0", name="display_name_nonempty"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        sa.PrimaryKeyConstraint("id", name="pk_speakers"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_speakers_external_key_not_null",
        "speakers",
        ["external_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("external_key IS NOT NULL"),
    )

    op.create_table(
        "annotation_libraries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("owner_label", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
            name="namespace_format",
        ),
        sa.CheckConstraint("length(name) > 0", name="name_nonempty"),
        sa.PrimaryKeyConstraint("id", name="pk_annotation_libraries"),
        sa.UniqueConstraint("namespace", name="uq_annotation_libraries_namespace"),
        schema=SCHEMA,
    )

    op.create_table(
        "library_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("library_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("author", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.CheckConstraint(
            "version_label ~ '^[^[:space:]:]+$'",
            name="version_label_format",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lower_hex",
        ),
        sa.ForeignKeyConstraint(
            ["library_id"],
            [f"{SCHEMA}.annotation_libraries.id"],
            name="fk_library_versions_library_id_annotation_libraries",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_library_versions"),
        sa.UniqueConstraint(
            "library_id", "version_label", name="uq_library_versions_library_id_version_label"
        ),
        sa.UniqueConstraint(
            "library_id",
            "content_sha256",
            name="uq_library_versions_library_id_content_sha256",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_library_versions_library_id_created_at",
        "library_versions",
        ["library_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )

    op.create_table(
        "library_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("library_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("entry_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("allowed_geometry_types", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "attribute_schema",
            postgresql.JSONB(),
            server_default=sa.text('\'{"type":"object"}\'::jsonb'),
            nullable=False,
        ),
        sa.Column(
            "validation_hints",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "display_hints",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("position >= 0", name="position_nonnegative"),
        sa.CheckConstraint(
            "entry_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
            name="entry_key_format",
        ),
        sa.CheckConstraint("length(display_name) > 0", name="display_name_nonempty"),
        sa.CheckConstraint(
            "cardinality(allowed_geometry_types) > 0 "
            "AND array_position(allowed_geometry_types, NULL) IS NULL "
            "AND allowed_geometry_types <@ "
            "ARRAY['point','time_interval','time_frequency_box',"
            "'time_frequency_polygon']::text[]",
            name="allowed_geometry_types",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attribute_schema) = 'object'",
            name="attribute_schema_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_hints) = 'object'",
            name="validation_hints_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(display_hints) = 'object'",
            name="display_hints_object",
        ),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        sa.ForeignKeyConstraint(
            ["library_version_id"],
            [f"{SCHEMA}.library_versions.id"],
            name="fk_library_entries_library_version_id_library_versions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_library_entries"),
        sa.UniqueConstraint(
            "library_version_id",
            "entry_key",
            name="uq_library_entries_library_version_id_entry_key",
        ),
        sa.UniqueConstraint(
            "library_version_id",
            "position",
            name="uq_library_entries_library_version_id_position",
        ),
        sa.UniqueConstraint(
            "id",
            "library_version_id",
            name="uq_library_entries_id_library_version_id",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "recordings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("audio_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("head_revision_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audio_asset_id"],
            [f"{SCHEMA}.audio_assets.id"],
            name="fk_recordings_audio_asset_id_audio_assets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recordings"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_recordings_audio_asset_id",
        "recordings",
        ["audio_asset_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "recording_revisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("recording_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", postgresql.UUID(as_uuid=True)),
        sa.Column("schema_version", sa.Text(), server_default=sa.text("'1.0'"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("default_speaker_ref", postgresql.UUID(as_uuid=True)),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("author", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.CheckConstraint("revision_number > 0", name="revision_number_positive"),
        sa.CheckConstraint(
            "(revision_number = 1 AND parent_revision_id IS NULL) OR "
            "(revision_number > 1 AND parent_revision_id IS NOT NULL)",
            name="parent_shape",
        ),
        sa.CheckConstraint("schema_version = '1.0'", name="schema_version_1_0"),
        sa.CheckConstraint("length(name) > 0", name="name_nonempty"),
        sa.CheckConstraint("length(language) > 0", name="language_nonempty"),
        sa.ForeignKeyConstraint(
            ["recording_id"],
            [f"{SCHEMA}.recordings.id"],
            name="fk_recording_revisions_recording_id_recordings",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["default_speaker_ref"],
            [f"{SCHEMA}.speakers.id"],
            name="fk_recording_revisions_default_speaker_ref_speakers",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recording_revisions"),
        sa.UniqueConstraint(
            "recording_id",
            "revision_number",
            name="uq_recording_revisions_recording_id_revision_number",
        ),
        sa.UniqueConstraint("recording_id", "id", name="uq_recording_revisions_recording_id_id"),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_recording_revisions_parent_same_recording",
        "recording_revisions",
        "recording_revisions",
        ["recording_id", "parent_revision_id"],
        ["recording_id", "id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_recording_revisions_parent_revision_id_not_null",
        "recording_revisions",
        ["parent_revision_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("parent_revision_id IS NOT NULL"),
    )
    op.create_index(
        "ix_recording_revisions_recording_id_created_at",
        "recording_revisions",
        ["recording_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_recordings_id_head_revision_id_recording_revisions",
        "recordings",
        "recording_revisions",
        ["id", "head_revision_id"],
        ["recording_id", "id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "recording_revision_libraries",
        sa.Column("recording_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("library_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("position >= 0", name="position_nonnegative"),
        sa.ForeignKeyConstraint(
            ["recording_revision_id"],
            [f"{SCHEMA}.recording_revisions.id"],
            name="fk_revision_libraries_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["library_version_id"],
            [f"{SCHEMA}.library_versions.id"],
            name="fk_revision_libraries_library_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "recording_revision_id",
            "library_version_id",
            name="pk_recording_revision_libraries",
        ),
        sa.UniqueConstraint(
            "recording_revision_id",
            "position",
            name="uq_recording_revision_libraries_recording_revision_id_position",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_recording_revision_libraries_library_version_id",
        "recording_revision_libraries",
        ["library_version_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "annotations",
        sa.Column("recording_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("annotation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("library_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("library_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("geometry_type", sa.Text(), nullable=False),
        sa.Column("start_sample", sa.BigInteger(), nullable=False),
        sa.Column("end_sample", sa.BigInteger()),
        sa.Column("min_frequency_hz", sa.Float()),
        sa.Column("max_frequency_hz", sa.Float()),
        sa.Column("polygon_vertices", postgresql.JSONB()),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float()),
        sa.Column("note", sa.Text()),
        sa.Column("provenance_ref", sa.Text()),
        sa.CheckConstraint("position >= 0", name="position_nonnegative"),
        sa.CheckConstraint(
            "geometry_type IN ('point','time_interval','time_frequency_box',"
            "'time_frequency_polygon')",
            name="geometry_type",
        ),
        sa.CheckConstraint("start_sample >= 0", name="start_sample_nonnegative"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        sa.CheckConstraint("jsonb_typeof(attributes) = 'object'", name="attributes_object"),
        sa.CheckConstraint(
            "(geometry_type = 'point' AND end_sample IS NULL "
            "AND min_frequency_hz IS NULL AND max_frequency_hz IS NULL "
            "AND polygon_vertices IS NULL) OR "
            "(geometry_type = 'time_interval' AND end_sample > start_sample "
            "AND min_frequency_hz IS NULL AND max_frequency_hz IS NULL "
            "AND polygon_vertices IS NULL) OR "
            "(geometry_type = 'time_frequency_box' AND end_sample > start_sample "
            "AND min_frequency_hz >= 0 "
            "AND min_frequency_hz < 'Infinity'::double precision "
            "AND max_frequency_hz > min_frequency_hz "
            "AND max_frequency_hz < 'Infinity'::double precision "
            "AND polygon_vertices IS NULL) OR "
            "(geometry_type = 'time_frequency_polygon' AND end_sample > start_sample "
            "AND min_frequency_hz >= 0 "
            "AND min_frequency_hz < 'Infinity'::double precision "
            "AND max_frequency_hz > min_frequency_hz "
            "AND max_frequency_hz < 'Infinity'::double precision "
            "AND jsonb_typeof(polygon_vertices) = 'array' "
            "AND jsonb_array_length(polygon_vertices) >= 3)",
            name="geometry_shape",
        ),
        sa.ForeignKeyConstraint(
            ["recording_revision_id", "library_version_id"],
            [
                f"{SCHEMA}.recording_revision_libraries.recording_revision_id",
                f"{SCHEMA}.recording_revision_libraries.library_version_id",
            ],
            name="fk_annotations_pinned_library",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["library_entry_id", "library_version_id"],
            [f"{SCHEMA}.library_entries.id", f"{SCHEMA}.library_entries.library_version_id"],
            name="fk_annotations_entry_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("recording_revision_id", "annotation_id", name="pk_annotations"),
        sa.UniqueConstraint(
            "recording_revision_id",
            "position",
            name="uq_annotations_recording_revision_id_position",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_annotations_recording_revision_id_start_sample",
        "annotations",
        ["recording_revision_id", "start_sample"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_annotations_library_entry_id_recording_revision_id",
        "annotations",
        ["library_entry_id", "recording_revision_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    global SCHEMA
    SCHEMA = _configured_schema()
    op.drop_table("annotations", schema=SCHEMA)
    op.drop_table("recording_revision_libraries", schema=SCHEMA)
    op.drop_constraint(
        "fk_recordings_id_head_revision_id_recording_revisions",
        "recordings",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("recording_revisions", schema=SCHEMA)
    op.drop_table("recordings", schema=SCHEMA)
    op.drop_table("library_entries", schema=SCHEMA)
    op.drop_table("library_versions", schema=SCHEMA)
    op.drop_table("annotation_libraries", schema=SCHEMA)
    op.drop_table("speakers", schema=SCHEMA)
    op.drop_table("audio_assets", schema=SCHEMA)
    op.execute(f"DROP SCHEMA {SCHEMA}")
