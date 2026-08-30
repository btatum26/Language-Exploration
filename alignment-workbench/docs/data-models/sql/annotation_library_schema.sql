-- Representative annotation-library DDL.
-- Apply through an Alembic migration in registry-aligner for production use.
-- Apply this schema before recording_schema.sql.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE annotation_libraries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace text NOT NULL UNIQUE CHECK (
        namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'
    ),
    name text NOT NULL,
    description text,
    owner_label text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE annotation_library_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id uuid NOT NULL REFERENCES annotation_libraries(id),
    version_label text NOT NULL CHECK (
        version_label ~ '^[^[:space:]:]+$'
    ),
    content_sha256 varchar(64) NOT NULL,
    author text,
    description text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (library_id, version_label),
    UNIQUE (library_id, content_sha256),
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE annotation_library_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    library_version_id uuid NOT NULL REFERENCES annotation_library_versions(id),
    entry_key text NOT NULL CHECK (
        entry_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]*$'
    ),
    display_name text NOT NULL,
    description text NOT NULL,
    allowed_geometry_types jsonb NOT NULL,
    attribute_schema jsonb NOT NULL DEFAULT '{"type":"object"}'::jsonb,
    validation_hints jsonb NOT NULL DEFAULT '{}'::jsonb,
    display_hints jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (library_version_id, entry_key),
    CHECK (
        jsonb_typeof(allowed_geometry_types) = 'array'
        AND jsonb_array_length(allowed_geometry_types) > 0
        AND allowed_geometry_types <@ '[
            "point",
            "time_interval",
            "time_frequency_box",
            "time_frequency_polygon"
        ]'::jsonb
    )
);

-- Published library versions and entries are immutable. The production
-- migration should enforce this with triggers or equivalent repository rules.
