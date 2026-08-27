"""Add effective revision targets and optimistic topology versions.

Revision ID: 20260826_0005
Revises: 20260824_0004
"""

from sqlalchemy import BigInteger, Column, inspect, text
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "20260826_0005"
down_revision = "20260824_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("segment_revisions")}
    if "effective_target_segment_ids" not in columns:
        op.add_column(
            "segment_revisions",
            Column(
                "effective_target_segment_ids",
                JSONB(),
                nullable=False,
                server_default=text("'[]'::jsonb"),
            ),
        )
        op.execute(
            """
            UPDATE segment_revisions
            SET effective_target_segment_ids = CASE
                WHEN jsonb_array_length(affected_segment_ids) > 0 THEN affected_segment_ids
                ELSE jsonb_build_array(segment_id::text)
            END
            """
        )
    if "base_topology_version" not in columns:
        op.add_column("segment_revisions", Column("base_topology_version", BigInteger()))


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("segment_revisions")}
    if "base_topology_version" in columns:
        op.drop_column("segment_revisions", "base_topology_version")
    if "effective_target_segment_ids" in columns:
        op.drop_column("segment_revisions", "effective_target_segment_ids")
