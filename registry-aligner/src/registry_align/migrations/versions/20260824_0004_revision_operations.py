"""Represent immutable split and merge segment revisions.

Revision ID: 20260824_0004
Revises: 20260824_0003
"""

from sqlalchemy import Column, String, inspect, text
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260824_0004"
down_revision = "20260824_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("segment_revisions")}
    if "base_run_id" not in columns:
        op.add_column(
            "segment_revisions",
            Column("base_run_id", String(255), nullable=True),
        )
        op.execute("UPDATE segment_revisions SET base_run_id = 'legacy'")
        op.alter_column("segment_revisions", "base_run_id", nullable=False)
    if "operation" not in columns:
        op.add_column(
            "segment_revisions",
            Column("operation", String(32), nullable=False, server_default=text("'update'")),
        )
        op.add_column(
            "segment_revisions",
            Column(
                "affected_segment_ids",
                JSONB(),
                nullable=False,
                server_default=text("'[]'::jsonb"),
            ),
        )
        op.add_column(
            "segment_revisions",
            Column(
                "replacement_segments",
                JSONB(),
                nullable=False,
                server_default=text("'[]'::jsonb"),
            ),
        )
        op.create_check_constraint(
            "ck_revision_operation",
            "segment_revisions",
            "operation IN ('update','split','merge')",
        )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("segment_revisions")}
    if "operation" in columns:
        op.drop_constraint("ck_revision_operation", "segment_revisions", type_="check")
        op.drop_column("segment_revisions", "replacement_segments")
        op.drop_column("segment_revisions", "affected_segment_ids")
        op.drop_column("segment_revisions", "operation")
    if "base_run_id" in columns:
        op.drop_column("segment_revisions", "base_run_id")
