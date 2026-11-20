"""phone change alerts

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('phone_changes', sa.Column('old_phone_enc', sa.LargeBinary(), nullable=True))
    op.add_column('relay_requests', sa.Column('ref_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('relay_requests', 'ref_id')
    op.drop_column('phone_changes', 'old_phone_enc')
