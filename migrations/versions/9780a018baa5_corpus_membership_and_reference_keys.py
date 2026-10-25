"""corpus membership and reference keys

Revision ID: 9780a018baa5
Revises: 17fb3d1f4207
Create Date: 2026-10-18 21:33:09.313209

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9780a018baa5'
down_revision: Union[str, Sequence[str], None] = '17fb3d1f4207'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (ADR-029).

    corpus / corpus_paper record which papers form a working corpus and how
    they were chosen (query, seed, date). Members are picked before they are
    harvested, so corpus_paper keeps the source IDs and paper_id fills in later.
    reference gains a natural key and the PMID that cascade stage 2 needs.
    """
    op.create_table('corpus',
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('query', sa.Text(), nullable=False),
    sa.Column('seed', sa.BigInteger(), nullable=False),
    sa.Column('hit_count', sa.Integer(), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.Column('selected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('label')
    )
    op.create_table('corpus_paper',
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('ordinal', sa.Integer(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_id', sa.Text(), nullable=False),
    sa.Column('pmcid', sa.Text(), nullable=False),
    sa.Column('paper_id', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['label'], ['corpus.label'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['paper_id'], ['paper.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('label', 'ordinal'),
    sa.UniqueConstraint('label', 'pmcid', name='corpus_paper_label_pmcid_key')
    )
    op.create_index('corpus_paper_paper', 'corpus_paper', ['paper_id'], unique=False)
    op.add_column('reference', sa.Column('parsed_pmid', sa.Text(), nullable=True))
    op.add_column('reference', sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_unique_constraint(
        'reference_citing_ordinal_key', 'reference', ['citing_paper_id', 'ordinal']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('reference_citing_ordinal_key', 'reference', type_='unique')
    op.drop_column('reference', 'raw')
    op.drop_column('reference', 'parsed_pmid')
    op.drop_index('corpus_paper_paper', table_name='corpus_paper')
    op.drop_table('corpus_paper')
    op.drop_table('corpus')
