"""phase 2 passages, citation contexts, claims and evaluation items

Revision ID: 6bb29bc195ff
Revises: b41d7c2e9a10
Create Date: 2026-10-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6bb29bc195ff'
down_revision: Union[str, Sequence[str], None] = 'b41d7c2e9a10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (§10.2, ADR-044).

    pgvector isn't a trusted extension: on the local container a superuser
    has already installed it (ADR-043), and this statement is a no-op. Like
    pg_trgm in the first migration, downgrade leaves it installed.
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        'passage',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('paper_id', sa.BigInteger(),
                  sa.ForeignKey('paper.id', ondelete='CASCADE'), nullable=False),
        sa.Column('splitter_ver', sa.Text(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('section', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.UniqueConstraint('paper_id', 'splitter_ver', 'ordinal',
                            name='passage_paper_ver_ordinal_key'),
    )

    op.create_table(
        'passage_embedding',
        sa.Column('passage_id', sa.BigInteger(),
                  sa.ForeignKey('passage.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('model', sa.Text(), primary_key=True),
        sa.Column('embedding', Vector(768), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
    )
    op.create_index('passage_embedding_hnsw', 'passage_embedding', ['embedding'],
                    postgresql_using='hnsw',
                    postgresql_ops={'embedding': 'vector_cosine_ops'})

    op.create_table(
        'citation_context',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('citing_paper_id', sa.BigInteger(),
                  sa.ForeignKey('paper.id', ondelete='CASCADE'), nullable=False),
        sa.Column('extractor_ver', sa.Text(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('section', sa.Text(), nullable=True),
        sa.Column('marker', sa.Text(), nullable=True),
        sa.Column('sentence', sa.Text(), nullable=False),
        sa.Column('context_text', sa.Text(), nullable=False),
        sa.UniqueConstraint('citing_paper_id', 'extractor_ver', 'ordinal',
                            name='citation_context_paper_ver_ordinal_key'),
    )

    op.create_table(
        'citation_context_reference',
        sa.Column('citation_context_id', sa.BigInteger(),
                  sa.ForeignKey('citation_context.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('reference_id', sa.BigInteger(),
                  sa.ForeignKey('reference.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_index('citation_context_reference_ref', 'citation_context_reference',
                    ['reference_id'])

    op.create_table(
        'claim',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('citation_context_id', sa.BigInteger(),
                  sa.ForeignKey('citation_context.id', ondelete='CASCADE'), nullable=False),
        sa.Column('skeleton', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('extractor', sa.Text(), nullable=False),
        sa.Column('extractor_ver', sa.Text(), nullable=False),
        sa.Column('valid', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.UniqueConstraint('citation_context_id', 'extractor', 'extractor_ver',
                            name='claim_context_extractor_key'),
    )

    op.create_table(
        'eval_item',
        sa.Column('id', sa.BigInteger(), primary_key=True),
        sa.Column('set_name', sa.Text(), nullable=False),
        sa.Column('split', sa.Text(), nullable=False),
        sa.Column('claim_text', sa.Text(), nullable=False),
        sa.Column('source_paper_id', sa.BigInteger(), sa.ForeignKey('paper.id'), nullable=True),
        sa.Column('label', sa.Text(), nullable=True),
        sa.Column('corruption_type', sa.Text(), nullable=True),
        sa.Column('license', sa.Text(), nullable=False),
        sa.Column('origin', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
    )
    op.create_index('eval_item_set_split', 'eval_item', ['set_name', 'split'])

    op.create_table(
        'eval_item_gold_passage',
        sa.Column('eval_item_id', sa.BigInteger(),
                  sa.ForeignKey('eval_item.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('passage_id', sa.BigInteger(),
                  sa.ForeignKey('passage.id', ondelete='RESTRICT'), primary_key=True),
    )
    op.create_index('eval_item_gold_passage_passage', 'eval_item_gold_passage',
                    ['passage_id'])


def downgrade() -> None:
    """Downgrade schema. The vector extension stays, as pg_trgm does."""
    op.drop_index('eval_item_gold_passage_passage', table_name='eval_item_gold_passage')
    op.drop_table('eval_item_gold_passage')
    op.drop_index('eval_item_set_split', table_name='eval_item')
    op.drop_table('eval_item')
    op.drop_table('claim')
    op.drop_index('citation_context_reference_ref', table_name='citation_context_reference')
    op.drop_table('citation_context_reference')
    op.drop_table('citation_context')
    op.drop_index('passage_embedding_hnsw', table_name='passage_embedding')
    op.drop_table('passage_embedding')
    op.drop_table('passage')
