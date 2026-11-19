"""citation spans in each context's sentence; invalid claim answers kept

`citation_context.sentence_cites` holds where every citation group sits in
the citing sentence, as [start, end] offsets, from context extractor 1.1.0
on (NULL before). Claim extractor 1.1.0 uses it to accept a statement copied
with its markers left out.

`claim.answer` and `claim.errors` hold an invalid claim's last answer and
the rules it broke, so a later rule can re-check it without asking again.
A valid claim leaves both NULL; its answer is the skeleton (ADR-056).

Adding nullable columns rewrites nothing, and the downgrade drops them.

Revision ID: e5a7c3d91b20
Revises: d1219fa0eeed
Create Date: 2026-11-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e5a7c3d91b20'
down_revision: Union[str, Sequence[str], None] = 'd1219fa0eeed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (ADR-056)."""
    op.add_column('citation_context',
                  sa.Column('sentence_cites', postgresql.JSONB(), nullable=True))
    op.add_column('claim', sa.Column('answer', sa.Text(), nullable=True))
    op.add_column('claim', sa.Column('errors', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('claim', 'errors')
    op.drop_column('claim', 'answer')
    op.drop_column('citation_context', 'sentence_cites')
