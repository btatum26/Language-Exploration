"""Add the immutable remote source-audio storage key.

Revision ID: 20260824_0002
Revises: 20260824_0001
"""

from sqlalchemy import Column, Text, inspect

from alembic import op

revision = "20260824_0002"
down_revision = "20260824_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in inspect(connection).get_columns("audio_assets")}
    if "remote_storage_key" not in columns:
        op.add_column("audio_assets", Column("remote_storage_key", Text(), nullable=True))
        op.execute(
            "UPDATE audio_assets "
            "SET remote_storage_key = substring(sha256 FROM 1 FOR 2) || '/' || sha256"
        )
        op.alter_column("audio_assets", "remote_storage_key", nullable=False)
        op.create_unique_constraint(
            "uq_audio_assets_remote_storage_key", "audio_assets", ["remote_storage_key"]
        )
        op.create_check_constraint(
            "ck_audio_assets_remote_storage_key",
            "audio_assets",
            "remote_storage_key <> ''",
        )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("audio_assets")}
    if "remote_storage_key" in columns:
        op.drop_constraint("ck_audio_assets_remote_storage_key", "audio_assets", type_="check")
        op.drop_constraint("uq_audio_assets_remote_storage_key", "audio_assets", type_="unique")
        op.drop_column("audio_assets", "remote_storage_key")
