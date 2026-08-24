"""PostgreSQL metadata schema. Domain code never imports this module."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

speakers = Table(
    "speakers",
    metadata,
    Column("id", String(512), primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("language", String(64)),
    Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
)

recordings = Table(
    "recordings",
    metadata,
    Column("id", String(512), primary_key=True),
    Column("corpus_id", String(255), nullable=False),
    Column("speaker_id", String(512)),
    Column("language", String(64)),
    Column("current_version_id", UUID(as_uuid=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("corpus_id", "id", name="uq_recordings_corpus_id"),
)

audio_assets = Table(
    "audio_assets",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("sha256", String(64), nullable=False, unique=True),
    Column("original_extension", String(32), nullable=False),
    Column("codec", String(128), nullable=False),
    Column("sample_rate_hz", Integer, nullable=False),
    Column("channels", Integer, nullable=False),
    Column("duration_seconds", Float, nullable=False),
    Column("frame_count", BigInteger),
    Column("logical_path", Text, nullable=False),
    Column("remote_storage_key", Text, nullable=False, unique=True),
    Column("source_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("first_seen_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("length(sha256) = 64", name="ck_audio_assets_sha256"),
    CheckConstraint("sample_rate_hz > 0 AND channels > 0", name="ck_audio_assets_shape"),
    CheckConstraint("duration_seconds > 0", name="ck_audio_assets_duration"),
    CheckConstraint("remote_storage_key <> ''", name="ck_audio_assets_remote_storage_key"),
)

transcript_versions = Table(
    "transcript_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("raw_text", Text, nullable=False),
    Column("normalized_text", Text, nullable=False),
    Column("raw_sha256", String(64), nullable=False),
    Column("normalized_sha256", String(64), nullable=False),
    Column("normalizer_name", String(128), nullable=False),
    Column("normalizer_version", String(64), nullable=False),
    Column("tokenization", JSONB, nullable=False),
    Column("warnings", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint(
        "raw_sha256",
        "normalized_sha256",
        "normalizer_name",
        "normalizer_version",
        name="uq_transcript_content",
    ),
)

recording_versions = Table(
    "recording_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("recording_id", String(512), ForeignKey("recordings.id"), nullable=False),
    Column("audio_asset_id", UUID(as_uuid=True), ForeignKey("audio_assets.id"), nullable=False),
    Column(
        "transcript_version_id",
        UUID(as_uuid=True),
        ForeignKey("transcript_versions.id"),
        nullable=False,
    ),
    Column("language", String(64), nullable=False),
    Column("speaker_id", String(512)),
    Column("user_metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("content_fingerprint", String(64), nullable=False),
    Column("current_alignment_result_id", UUID(as_uuid=True)),
    Column("superseded_version_id", UUID(as_uuid=True), ForeignKey("recording_versions.id")),
    Column("ingestion_reason", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("recording_id", "content_fingerprint", name="uq_recording_content_state"),
    CheckConstraint("length(content_fingerprint) = 64", name="ck_recording_version_fingerprint"),
)

recordings.append_constraint(
    ForeignKeyConstraint(
        [recordings.c.current_version_id],
        [recording_versions.c.id],
        name="fk_recordings_current_version",
        use_alter=True,
    )
)

alignment_results = Table(
    "alignment_results",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column(
        "recording_version_id",
        UUID(as_uuid=True),
        ForeignKey("recording_versions.id"),
        nullable=False,
    ),
    Column("backend_name", String(128), nullable=False),
    Column("backend_version", String(128), nullable=False),
    Column("acoustic_model", String(512), nullable=False),
    Column("dictionary_identity", String(512), nullable=False),
    Column("normalizer_identity", String(255), nullable=False),
    Column("audio_profile", String(64), nullable=False),
    Column("pipeline_version", String(64), nullable=False),
    Column("processing_fingerprint", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("qc_summary", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("lease_owner", String(255)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("completed_at", DateTime(timezone=True)),
    UniqueConstraint(
        "recording_version_id", "processing_fingerprint", name="uq_alignment_processing"
    ),
    CheckConstraint("status IN ('running','complete','failed')", name="ck_alignment_result_status"),
)

recording_versions.append_constraint(
    ForeignKeyConstraint(
        [recording_versions.c.current_alignment_result_id],
        [alignment_results.c.id],
        name="fk_recording_versions_current_alignment",
        use_alter=True,
    )
)

segments = Table(
    "segments",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column(
        "alignment_result_id",
        UUID(as_uuid=True),
        ForeignKey("alignment_results.id"),
        nullable=False,
    ),
    Column("kind", String(32), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("label", Text, nullable=False),
    Column("backend_label", Text, nullable=False),
    Column("start_sample", BigInteger, nullable=False),
    Column("end_sample", BigInteger, nullable=False),
    Column("timebase_sample_rate_hz", Integer, nullable=False),
    Column("parent_segment_id", UUID(as_uuid=True), ForeignKey("segments.id")),
    Column("token_mappings", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("confidence", Float),
    Column("review_state", String(32), nullable=False, server_default=text("'automatic'")),
    Column("model_provenance", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    UniqueConstraint("alignment_result_id", "kind", "ordinal", name="uq_segment_ordinal"),
    CheckConstraint("kind IN ('word','phone','silence','utterance')", name="ck_segment_kind"),
    CheckConstraint("start_sample >= 0 AND end_sample > start_sample", name="ck_segment_bounds"),
    CheckConstraint("timebase_sample_rate_hz > 0", name="ck_segment_timebase"),
    CheckConstraint(
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_segment_confidence"
    ),
)

segment_revisions = Table(
    "segment_revisions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("segment_id", UUID(as_uuid=True), ForeignKey("segments.id"), nullable=False),
    Column("base_run_id", String(255), nullable=False),
    Column("start_sample_override", BigInteger),
    Column("end_sample_override", BigInteger),
    Column("label_override", Text),
    Column("review_state", String(32), nullable=False),
    Column("author", String(255)),
    Column("reason", Text),
    Column("operation", String(32), nullable=False, server_default=text("'update'")),
    Column("affected_segment_ids", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("replacement_segments", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("review_state IN ('proposed','accepted','rejected')", name="ck_revision_state"),
    CheckConstraint("operation IN ('update','split','merge')", name="ck_revision_operation"),
)

runs = Table(
    "runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("finished_at", DateTime(timezone=True)),
    Column("configuration_fingerprint", String(64), nullable=False),
    Column("backend_summary", JSONB, nullable=False),
    Column("aggregate_counts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error_summary", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("invoking_host", String(255)),
    CheckConstraint("status IN ('running','complete','partial','failed')", name="ck_run_status"),
)

run_items = Table(
    "run_items",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("recording_id", String(512), nullable=False),
    Column("recording_version_id", UUID(as_uuid=True), ForeignKey("recording_versions.id")),
    Column("alignment_result_id", UUID(as_uuid=True), ForeignKey("alignment_results.id")),
    Column("outcome", String(32), nullable=False),
    Column("detail", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    UniqueConstraint("run_id", "recording_id", name="uq_run_item_recording"),
    CheckConstraint(
        "outcome IN ('skipped','unchanged','version-created',"
        "'alignment-reused','processed','failed')",
        name="ck_run_item_outcome",
    ),
)

issues = Table(
    "issues",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("recording_id", String(512)),
    Column("code", String(128), nullable=False),
    Column("severity", String(32), nullable=False),
    Column("stage", String(64), nullable=False),
    Column("message", Text, nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    CheckConstraint("severity IN ('info','warning','error')", name="ck_issue_severity"),
)

Index(
    "ix_recording_versions_created_at",
    recording_versions.c.recording_id,
    recording_versions.c.created_at.desc(),
)
Index("ix_alignment_status_lease", alignment_results.c.status, alignment_results.c.lease_expires_at)
Index("ix_segments_result_bounds", segments.c.alignment_result_id, segments.c.start_sample)
Index(
    "ix_revisions_segment_created",
    segment_revisions.c.segment_id,
    segment_revisions.c.created_at.desc(),
)
Index("ix_runs_started_at", runs.c.started_at.desc())
Index("ix_speakers_display_name", speakers.c.display_name)
