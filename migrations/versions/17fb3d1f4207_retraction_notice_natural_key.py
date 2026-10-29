"""retraction_notice natural key

Revision ID: 17fb3d1f4207
Revises: 157fbb99af3c
Create Date: 2026-10-18 15:10:22.510043

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '17fb3d1f4207'
down_revision: Union[str, Sequence[str], None] = '157fbb99af3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Idempotent upserts need a natural key (§9.3). DOI/type combinations aren't
    unique in Retraction Watch; its Record ID is. Nullable so a future source
    without record IDs can still write rows (NULLs never conflict).
    """
    op.add_column('retraction_notice', sa.Column('source_record_id', sa.Text(), nullable=True))
    op.create_unique_constraint(
        'retraction_notice_source_record_key', 'retraction_notice',
        ['source', 'source_record_id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('retraction_notice_source_record_key', 'retraction_notice', type_='unique')
    op.drop_column('retraction_notice', 'source_record_id')
