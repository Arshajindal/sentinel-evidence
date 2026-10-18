"""finding subject is a paper or a trial

Revision ID: 157fbb99af3c
Revises: c9051bc4f780
Create Date: 2026-10-18 14:46:35.019736

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '157fbb99af3c'
down_revision: Union[str, Sequence[str], None] = 'c9051bc4f780'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    A finding is about either a paper or a trial (sentinel-project.md §8):
    C05 runs on registry data with no paper, so paper_id can't be required.
    """
    op.alter_column('finding', 'paper_id', existing_type=sa.BigInteger(), nullable=True)
    op.add_column('finding', sa.Column('nct_id', sa.Text(), nullable=True))
    op.create_foreign_key(
        'finding_nct_id_fkey', 'finding', 'trial',
        ['nct_id'], ['nct_id'], ondelete='CASCADE',
    )
    op.create_check_constraint(
        'finding_one_subject', 'finding', 'num_nonnulls(paper_id, nct_id) = 1'
    )
    op.create_index('finding_nct', 'finding', ['nct_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Trial findings (paper_id NULL) can't exist in the old schema. If any are
    present, the NOT NULL step fails and the transaction rolls back; they are
    never deleted silently.
    """
    op.drop_index('finding_nct', table_name='finding')
    op.drop_constraint('finding_one_subject', 'finding', type_='check')
    op.drop_constraint('finding_nct_id_fkey', 'finding', type_='foreignkey')
    op.drop_column('finding', 'nct_id')
    op.alter_column('finding', 'paper_id', existing_type=sa.BigInteger(), nullable=False)
