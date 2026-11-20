"""phone changes

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'phone_changes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product', sa.String(length=32), nullable=False),
        sa.Column('new_phone_enc', sa.LargeBinary(), nullable=False),
        sa.Column('new_phone_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='requested'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_phone_changes_user_id', 'phone_changes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_phone_changes_user_id', table_name='phone_changes')
    op.drop_table('phone_changes')
