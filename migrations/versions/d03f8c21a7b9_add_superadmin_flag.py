"""add superadmin flag to users

Revision ID: d03f8c21a7b9
Revises: c91f0a62d4e1
Create Date: 2026-08-08 21:35:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd03f8c21a7b9'
down_revision: Union[str, None] = 'c91f0a62d4e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'is_superadmin',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'is_superadmin')
