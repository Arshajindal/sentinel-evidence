"""drop the unused cosine HNSW index on passage_embedding

Dense search is exact since ADR-050: candidates are scored in a materialised
CTE, so no index can serve the ORDER BY, and the HNSW index had cut filtered
searches short. The index is unused but still costs disk (about 1 GB at
271,416 vectors) and work on every insert (ADR-052).

The downgrade rebuilds it exactly as 6bb29bc195ff made it. On a full table
that takes minutes, not seconds.

Revision ID: d1219fa0eeed
Revises: 8d0bd1c5a522
Create Date: 2026-11-11 22:08:20.674173

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1219fa0eeed'
down_revision: Union[str, Sequence[str], None] = '8d0bd1c5a522'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (ADR-052)."""
    op.drop_index('passage_embedding_hnsw', table_name='passage_embedding')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index('passage_embedding_hnsw', 'passage_embedding', ['embedding'],
                    postgresql_using='hnsw',
                    postgresql_ops={'embedding': 'vector_cosine_ops'})
