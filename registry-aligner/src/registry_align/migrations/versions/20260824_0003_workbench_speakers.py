"""Add a durable speaker catalog for interactive ingestion.

Revision ID: 20260824_0003
Revises: 20260824_0002
"""

from sqlalchemy import inspect

from alembic import op
from registry_align.storage.tables import speakers

revision = "20260824_0003"
down_revision = "20260824_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if not inspect(connection).has_table("speakers"):
        speakers.create(bind=connection)
    op.execute(
        "INSERT INTO speakers (id, display_name, language) "
        "SELECT speaker_id, speaker_id, min(language) "
        "FROM recording_versions WHERE speaker_id IS NOT NULL "
        "GROUP BY speaker_id ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    if inspect(op.get_bind()).has_table("speakers"):
        speakers.drop(bind=op.get_bind())
