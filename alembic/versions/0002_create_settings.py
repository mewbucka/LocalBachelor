"""create settings

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 21:40:00.000000

The settings table (roadmap 1.4, architecture §3): per-user settings
with admin-default fallback — ``owner_id NULL`` marks the admin-default
row. Task 1.4 uses only the ``theme`` key; the full typed settings store
(tasks 2.1/2.2/2.6) reuses this table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column(
            'owner_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=True,
        ),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'key'),
    )
    op.create_index(op.f('ix_settings_owner_id'), 'settings', ['owner_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_settings_owner_id'), table_name='settings')
    op.drop_table('settings')
