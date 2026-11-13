"""Allow payment callbacks with no matching payment

Revision ID: e5b2c8a1d9f3
Revises: c4e1a9d7f2b6
Create Date: 2026-10-09 12:00:00.000000

Signed NOWPayments IPNs that match no payment were only written to the log
file, which Vercel keeps briefly. Making payment_db_id nullable lets them be
stored in payment_callbacks for review.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b2c8a1d9f3'
down_revision = 'c4e1a9d7f2b6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment_callbacks', schema=None) as batch_op:
        batch_op.alter_column('payment_db_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    # Unmatched callbacks have no payment to belong to — remove them first
    op.execute("DELETE FROM payment_callbacks WHERE payment_db_id IS NULL")
    with op.batch_alter_table('payment_callbacks', schema=None) as batch_op:
        batch_op.alter_column('payment_db_id', existing_type=sa.Integer(), nullable=False)
