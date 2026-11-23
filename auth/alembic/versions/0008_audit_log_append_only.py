"""audit_log is append-only (trigger)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

from app import audit_guard

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for stmt in audit_guard.CREATE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in audit_guard.DROP:
        op.execute(stmt)
