-- Representative recording and signal-annotation DDL.
-- Apply through an Alembic migration in registry-aligner for production use.
-- Requires annotation_library_schema.sql.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE audio_assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sha256 varchar(64) NOT NULL UNIQUE,
    storage_key text NOT NULL UNIQUE,
    logical_path text,
    media_type text,
    original_extension text,
    codec text,
    sample_rate_hz integer NOT NULL CHECK (sample_rate_hz > 0),
    channels integer NOT NULL CHECK (channels > 0),
    frame_count bigint NOT NULL CHECK (frame_count > 0),
    source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE speakers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_key text UNIQUE,
    display_name text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE recordings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    audio_asset_id uuid NOT NULL REFERENCES audio_assets(id),
    head_revision_id uuid,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE recording_revisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recordings(id),
    revision_number bigint NOT NULL CHECK (revision_number > 0),
    parent_revision_id uuid REFERENCES recording_revisions(id),
    name text NOT NULL,
    default_speaker_id uuid REFERENCES speakers(id),
    language_tag text NOT NULL,
    author text,
    message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (recording_id, revision_number)
);

ALTER TABLE recordings
ADD CONSTRAINT fk_recordings_head_revision
FOREIGN KEY (head_revision_id) REFERENCES recording_revisions(id);

CREATE TABLE recording_revision_libraries (
    recording_revision_id uuid NOT NULL REFERENCES recording_revisions(id),
    library_version_id uuid NOT NULL REFERENCES annotation_library_versions(id),
    PRIMARY KEY (recording_revision_id, library_version_id)
);

CREATE TABLE annotation_identities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    recording_id uuid NOT NULL REFERENCES recordings(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE signal_annotation_states (
    recording_revision_id uuid NOT NULL REFERENCES recording_revisions(id),
    annotation_id uuid NOT NULL REFERENCES annotation_identities(id),
    library_entry_id uuid NOT NULL REFERENCES annotation_library_entries(id),
    geometry_type text NOT NULL CHECK (
        geometry_type IN (
            'point',
            'time_interval',
            'time_frequency_box',
            'time_frequency_polygon'
        )
    ),
    start_sample bigint NOT NULL CHECK (start_sample >= 0),
    end_sample bigint,
    min_frequency_hz double precision,
    max_frequency_hz double precision,
    geometry_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence double precision CHECK (
        confidence IS NULL OR (confidence >= 0 AND confidence <= 1)
    ),
    provenance_ref text,
    note text,
    PRIMARY KEY (recording_revision_id, annotation_id),
    CHECK (
        (geometry_type = 'point' AND end_sample IS NULL)
        OR
        (
            geometry_type <> 'point'
            AND end_sample IS NOT NULL
            AND end_sample > start_sample
        )
    ),
    CHECK (
        (
            geometry_type IN ('point', 'time_interval')
            AND min_frequency_hz IS NULL
            AND max_frequency_hz IS NULL
        )
        OR
        (
            geometry_type IN ('time_frequency_box', 'time_frequency_polygon')
            AND min_frequency_hz IS NOT NULL
            AND max_frequency_hz IS NOT NULL
            AND min_frequency_hz >= 0
            AND max_frequency_hz > min_frequency_hz
        )
    )
);

CREATE INDEX ix_signal_annotation_states_time
ON signal_annotation_states(recording_revision_id, start_sample, end_sample);

CREATE INDEX ix_signal_annotation_states_library_entry
ON signal_annotation_states(library_entry_id);

CREATE INDEX ix_signal_annotation_states_identity_revision
ON signal_annotation_states(annotation_id, recording_revision_id);

-- The production migration or repository layer must also enforce:
--   * recordings.audio_asset_id cannot change;
--   * revision parents and heads belong to the same recording;
--   * annotation identities belong to the recording;
--   * referenced entries belong to the pinned library manifest;
--   * saved revisions, manifests, and annotations are immutable.
