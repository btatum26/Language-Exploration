"""Add an optional occurrence label without rewriting saved annotations.

Revision ID: 20260906_0003_annotation_label
Revises: 20260830_0002_storage_uri
"""

import sqlalchemy as sa

from alembic import op

revision = "20260906_0003_annotation_label"
down_revision = "20260830_0002_storage_uri"
branch_labels = None
depends_on = None


def _configured_schema() -> str:
    value = op.get_context().config.attributes.get("snapshot_schema", "registry_align")
    if not isinstance(value, str) or not value.replace("_", "").isalnum():
        raise RuntimeError(
            "snapshot migration schema must contain only letters, digits, underscores"
        )
    return value


def upgrade() -> None:
    op.add_column(
        "annotations", sa.Column("label", sa.Text(), nullable=True), schema=_configured_schema()
    )


def downgrade() -> None:
    op.drop_column("annotations", "label", schema=_configured_schema())
