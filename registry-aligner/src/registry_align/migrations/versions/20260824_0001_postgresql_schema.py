"""Fresh PostgreSQL metadata schema.

Revision ID: 20260824_0001
Revises: None
"""

from alembic import op
from registry_align.storage.tables import metadata

revision = "20260824_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind(), checkfirst=False)
