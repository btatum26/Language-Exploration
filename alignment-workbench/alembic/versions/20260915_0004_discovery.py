"""Optional discovery metadata on immutable revisions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260915_0004_discovery"
down_revision = "20260906_0003_annotation_label"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().config.attributes.get("snapshot_schema", "registry_align")
    op.add_column(
        "recording_revisions",
        sa.Column(
            "discovery_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().config.attributes.get("snapshot_schema", "registry_align")
    op.drop_column("recording_revisions", "discovery_metadata", schema=schema)
