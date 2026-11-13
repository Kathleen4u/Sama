"""Add credited_amount to transactions

Revision ID: c4e1a9d7f2b6
Revises: b2d8f4a61c3e
Create Date: 2026-10-08 12:00:00.000000

Tracks how much of a deposit has been added to the wallet so credits are
idempotent. Deposits already marked completed were credited their full
amount by the old code, so they are backfilled with credited_amount = amount
to stop a late IPN from crediting them again.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e1a9d7f2b6'
down_revision = 'b2d8f4a61c3e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('credited_amount', sa.Numeric(18, 2), nullable=False, server_default='0')
        )

    op.execute(
        "UPDATE transactions SET credited_amount = amount "
        "WHERE type = 'Deposit' AND status = 'completed'"
    )


def downgrade():
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_column('credited_amount')
