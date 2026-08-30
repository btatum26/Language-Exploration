"""ORM rows for immutable annotated-recording snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persistence.sqlalchemy.base import Base

SCHEMA = "registry_align"
GEOMETRY_TYPES = (
    "point",
    "time_interval",
    "time_frequency_box",
    "time_frequency_polygon",
)


class AudioAssetRow(Base):
    __tablename__ = "audio_assets"
    __table_args__ = (
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="sha256_lower_hex",
        ),
        CheckConstraint("sample_rate_hz > 0", name="sample_rate_positive"),
        CheckConstraint("frame_count > 0", name="frame_count_positive"),
        CheckConstraint("channels > 0", name="channels_positive"),
        CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="source_metadata_object",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    logical_path: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(Text)
    original_extension: Mapped[str | None] = mapped_column(Text)
    codec: Mapped[str | None] = mapped_column(Text)
    sample_rate_hz: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channels: Mapped[int] = mapped_column(Integer, nullable=False)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    recordings: Mapped[list[RecordingRow]] = relationship(
        back_populates="audio_asset", lazy="raise"
    )


class SpeakerRow(Base):
    __tablename__ = "speakers"
    __table_args__ = (
        CheckConstraint("length(display_name) > 0", name="display_name_nonempty"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        Index(
            "uq_speakers_external_key_not_null",
            "external_key",
            unique=True,
            postgresql_where=text("external_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    external_key: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AnnotationLibraryRow(Base):
    __tablename__ = "annotation_libraries"
    __table_args__ = (
        CheckConstraint(
            "namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
            name="namespace_format",
        ),
        CheckConstraint("length(name) > 0", name="name_nonempty"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    namespace: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    versions: Mapped[list[LibraryVersionRow]] = relationship(back_populates="library", lazy="raise")


class LibraryVersionRow(Base):
    __tablename__ = "library_versions"
    __table_args__ = (
        UniqueConstraint("library_id", "version_label"),
        UniqueConstraint("library_id", "content_sha256"),
        CheckConstraint(
            "version_label ~ '^[^[:space:]:]+$'",
            name="version_label_format",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lower_hex",
        ),
        Index("ix_library_versions_library_id_created_at", "library_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    library_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.annotation_libraries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    author: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    library: Mapped[AnnotationLibraryRow] = relationship(back_populates="versions", lazy="raise")
    entries: Mapped[list[LibraryEntryRow]] = relationship(
        back_populates="library_version", lazy="raise"
    )


class LibraryEntryRow(Base):
    __tablename__ = "library_entries"
    __table_args__ = (
        UniqueConstraint("library_version_id", "entry_key"),
        UniqueConstraint("library_version_id", "position"),
        UniqueConstraint("id", "library_version_id"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint(
            "entry_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'",
            name="entry_key_format",
        ),
        CheckConstraint("length(display_name) > 0", name="display_name_nonempty"),
        CheckConstraint(
            "cardinality(allowed_geometry_types) > 0 "
            "AND array_position(allowed_geometry_types, NULL) IS NULL "
            "AND allowed_geometry_types <@ "
            "ARRAY['point','time_interval','time_frequency_box',"
            "'time_frequency_polygon']::text[]",
            name="allowed_geometry_types",
        ),
        CheckConstraint(
            "jsonb_typeof(attribute_schema) = 'object'",
            name="attribute_schema_object",
        ),
        CheckConstraint(
            "jsonb_typeof(validation_hints) = 'object'",
            name="validation_hints_object",
        ),
        CheckConstraint(
            "jsonb_typeof(display_hints) = 'object'",
            name="display_hints_object",
        ),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    library_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.library_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_key: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_geometry_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    attribute_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text('\'{"type":"object"}\'::jsonb')
    )
    validation_hints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    display_hints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    library_version: Mapped[LibraryVersionRow] = relationship(
        back_populates="entries", lazy="raise"
    )


class RecordingRow(Base):
    __tablename__ = "recordings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "head_revision_id"],
            [f"{SCHEMA}.recording_revisions.recording_id", f"{SCHEMA}.recording_revisions.id"],
            name="fk_recordings_id_head_revision_id_recording_revisions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index("ix_recordings_audio_asset_id", "audio_asset_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    audio_asset_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.audio_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    head_revision_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    audio_asset: Mapped[AudioAssetRow] = relationship(back_populates="recordings", lazy="raise")
    revisions: Mapped[list[RecordingRevisionRow]] = relationship(
        back_populates="recording",
        foreign_keys="RecordingRevisionRow.recording_id",
        lazy="raise",
    )


class RecordingRevisionRow(Base):
    __tablename__ = "recording_revisions"
    __table_args__ = (
        UniqueConstraint("recording_id", "revision_number"),
        UniqueConstraint("recording_id", "id"),
        ForeignKeyConstraint(
            ["recording_id", "parent_revision_id"],
            [f"{SCHEMA}.recording_revisions.recording_id", f"{SCHEMA}.recording_revisions.id"],
            name="fk_recording_revisions_parent_same_recording",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision_number > 0", name="revision_number_positive"),
        CheckConstraint(
            "(revision_number = 1 AND parent_revision_id IS NULL) OR "
            "(revision_number > 1 AND parent_revision_id IS NOT NULL)",
            name="parent_shape",
        ),
        CheckConstraint("schema_version = '1.0'", name="schema_version_1_0"),
        CheckConstraint("length(name) > 0", name="name_nonempty"),
        CheckConstraint("length(language) > 0", name="language_nonempty"),
        Index(
            "uq_recording_revisions_parent_revision_id_not_null",
            "parent_revision_id",
            unique=True,
            postgresql_where=text("parent_revision_id IS NOT NULL"),
        ),
        Index(
            "ix_recording_revisions_recording_id_created_at",
            "recording_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    recording_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.recordings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'1.0'"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    default_speaker_ref: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.speakers.id", ondelete="RESTRICT"),
    )
    language: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    author: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)

    recording: Mapped[RecordingRow] = relationship(
        back_populates="revisions", foreign_keys=[recording_id], lazy="raise"
    )
    speaker: Mapped[SpeakerRow | None] = relationship(lazy="raise")


class RecordingRevisionLibraryRow(Base):
    __tablename__ = "recording_revision_libraries"
    __table_args__ = (
        PrimaryKeyConstraint("recording_revision_id", "library_version_id"),
        UniqueConstraint("recording_revision_id", "position"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        Index("ix_recording_revision_libraries_library_version_id", "library_version_id"),
    )

    recording_revision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.recording_revisions.id",
            name="fk_revision_libraries_revision",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    library_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            f"{SCHEMA}.library_versions.id",
            name="fk_revision_libraries_library_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class AnnotationRow(Base):
    __tablename__ = "annotations"
    __table_args__ = (
        PrimaryKeyConstraint("recording_revision_id", "annotation_id"),
        UniqueConstraint("recording_revision_id", "position"),
        ForeignKeyConstraint(
            ["recording_revision_id", "library_version_id"],
            [
                f"{SCHEMA}.recording_revision_libraries.recording_revision_id",
                f"{SCHEMA}.recording_revision_libraries.library_version_id",
            ],
            name="fk_annotations_pinned_library",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["library_entry_id", "library_version_id"],
            [f"{SCHEMA}.library_entries.id", f"{SCHEMA}.library_entries.library_version_id"],
            name="fk_annotations_entry_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint(
            "geometry_type IN ('point','time_interval','time_frequency_box',"
            "'time_frequency_polygon')",
            name="geometry_type",
        ),
        CheckConstraint("start_sample >= 0", name="start_sample_nonnegative"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        CheckConstraint("jsonb_typeof(attributes) = 'object'", name="attributes_object"),
        CheckConstraint(
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
        Index(
            "ix_annotations_recording_revision_id_start_sample",
            "recording_revision_id",
            "start_sample",
        ),
        Index(
            "ix_annotations_library_entry_id_recording_revision_id",
            "library_entry_id",
            "recording_revision_id",
        ),
    )

    recording_revision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    annotation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    library_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    library_entry_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    geometry_type: Mapped[str] = mapped_column(Text, nullable=False)
    start_sample: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_sample: Mapped[int | None] = mapped_column(BigInteger)
    min_frequency_hz: Mapped[float | None] = mapped_column(Float)
    max_frequency_hz: Mapped[float | None] = mapped_column(Float)
    polygon_vertices: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)
    provenance_ref: Mapped[str | None] = mapped_column(Text)
