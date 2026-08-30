-- Run this file as registry_align_owner after applying Alembic migrations.
-- It does not create roles and is intentionally separate from portable migrations.

BEGIN;

GRANT USAGE ON SCHEMA registry_align TO registry_align_app;

REVOKE ALL PRIVILEGES
ON ALL TABLES IN SCHEMA registry_align
FROM registry_align_app;

GRANT SELECT, INSERT
ON ALL TABLES IN SCHEMA registry_align
TO registry_align_app;

GRANT UPDATE (head_revision_id)
ON registry_align.recordings
TO registry_align_app;

REVOKE ALL PRIVILEGES
ON ALL SEQUENCES IN SCHEMA registry_align
FROM registry_align_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA registry_align
REVOKE ALL PRIVILEGES ON TABLES FROM registry_align_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA registry_align
GRANT SELECT, INSERT ON TABLES TO registry_align_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA registry_align
REVOKE ALL PRIVILEGES ON SEQUENCES FROM registry_align_app;

GRANT USAGE ON SCHEMA public TO registry_align_app;

REVOKE ALL PRIVILEGES
ON public.registry_align_snapshot_alembic_version
FROM registry_align_app;

GRANT SELECT
ON public.registry_align_snapshot_alembic_version
TO registry_align_app;

COMMIT;
