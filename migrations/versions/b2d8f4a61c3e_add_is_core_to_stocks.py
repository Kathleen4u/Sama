"""Add is_core to stocks

Revision ID: b2d8f4a61c3e
Revises: a7c3e9f2b104
Create Date: 2026-10-06 19:20:00.000000

The is_core column was added to the Stock model (commit b41cedd) without a
migration. Some databases already have it (added by hand), so this migration
only adds it when missing.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2d8f4a61c3e'
down_revision = 'a7c3e9f2b104'
branch_labels = None
depends_on = None


def _has_is_core():
    columns = sa.inspect(op.get_bind()).get_columns('stocks')
    return any(c['name'] == 'is_core' for c in columns)


def upgrade():
    if _has_is_core():
        return
    with op.batch_alter_table('stocks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_core', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    if not _has_is_core():
        return
    with op.batch_alter_table('stocks', schema=None) as batch_op:
        batch_op.drop_column('is_core')
