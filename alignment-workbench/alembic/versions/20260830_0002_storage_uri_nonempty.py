"""Require a nonempty audio storage URI.

Revision ID: 20260830_0002_storage_uri
Revises: 20260830_0001_snapshot
"""

from __future__ import annotations

from alembic import op

revision = "20260830_0002_storage_uri"
down_revision = "20260830_0001_snapshot"
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
    schema = _configured_schema()
    op.create_check_constraint(
        op.f("ck_audio_assets_storage_uri_nonempty"),
        "audio_assets",
        "length(storage_uri) > 0",
        schema=schema,
    )


def downgrade() -> None:
    schema = _configured_schema()
    op.drop_constraint(
        op.f("ck_audio_assets_storage_uri_nonempty"),
        "audio_assets",
        type_="check",
        schema=schema,
    )
