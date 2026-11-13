"""Add payout tx hash and network to withdrawal requests

Revision ID: 1b6e4d8a2c57
Revises: f7a3d2c9e1b4
Create Date: 2026-10-10 15:00:00.000000

Nullable columns only: withdrawals approved before this was tracked keep
NULL, and code running before this deploy is unaffected.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1b6e4d8a2c57'
down_revision = 'f7a3d2c9e1b4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('withdrawal_requests', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payout_tx_hash', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('payout_network', sa.String(length=30), nullable=True))


def downgrade():
    with op.batch_alter_table('withdrawal_requests', schema=None) as batch_op:
        batch_op.drop_column('payout_network')
        batch_op.drop_column('payout_tx_hash')
