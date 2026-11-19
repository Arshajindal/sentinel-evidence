"""text availability: what full text or abstract is held for each work

Revision ID: 8d0bd1c5a522
Revises: 6bb29bc195ff
Create Date: 2026-11-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8d0bd1c5a522'
down_revision: Union[str, Sequence[str], None] = '6bb29bc195ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (ADR-045)."""
    op.create_table(
        'text_availability',
        sa.Column('paper_id', sa.BigInteger(),
                  sa.ForeignKey('paper.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('lookup_key', sa.Text(), nullable=True),
        sa.Column('found', sa.Boolean(), nullable=False),
        sa.Column('pmcid', sa.Text(), nullable=True),
        sa.Column('is_oa', sa.Boolean(), nullable=True),
        sa.Column('in_pmc', sa.Boolean(), nullable=True),
        sa.Column('license', sa.Text(), nullable=True),
        sa.Column('fulltext', sa.Text(), nullable=False),
        sa.Column('fulltext_error', sa.Text(), nullable=True),
        sa.Column('abstract', sa.Text(), nullable=True),
        sa.Column('abstract_source', sa.Text(), nullable=True),
        sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
    )
    op.create_index('text_availability_fulltext', 'text_availability', ['fulltext'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('text_availability_fulltext', table_name='text_availability')
    op.drop_table('text_availability')
