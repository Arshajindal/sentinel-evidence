"""stage run timing

Revision ID: b41d7c2e9a10
Revises: 80034d5e56ce
Create Date: 2026-10-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b41d7c2e9a10'
down_revision: Union[str, Sequence[str], None] = '80034d5e56ce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (ADR-039).

    Start and end of each ingest, resolve and extract run, so `sentinel
    stats` reports wall time per stage (§9.8).
    """
    op.create_table(
        'stage_run',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('stage', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('corpus_label', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column('n_items', sa.Integer(), nullable=True),
        sa.Column('counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index('stage_run_corpus', 'stage_run', ['corpus_label', 'stage'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('stage_run_corpus', table_name='stage_run')
    op.drop_table('stage_run')
