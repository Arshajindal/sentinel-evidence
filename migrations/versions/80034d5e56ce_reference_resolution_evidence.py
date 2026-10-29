"""reference resolution evidence

Revision ID: 80034d5e56ce
Revises: 9780a018baa5
Create Date: 2026-10-25 15:24:41.108129

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '80034d5e56ce'
down_revision: Union[str, Sequence[str], None] = '9780a018baa5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (ADR-034).

    The resolution trail per reference: stages tried, near-misses with scores,
    lookups made, parser version. C01 shows it as evidence (§9.7).
    """
    op.add_column('reference', sa.Column(
        'resolution_evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reference', 'resolution_evidence')
