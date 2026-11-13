"""Add admin notes on user accounts

Revision ID: 2c9f5a7e3d18
Revises: 1b6e4d8a2c57
Create Date: 2026-10-10 17:00:00.000000

New table only — existing tables and running code are unaffected.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2c9f5a7e3d18'
down_revision = '1b6e4d8a2c57'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'admin_notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['admin_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('admin_notes', schema=None) as batch_op:
        batch_op.create_index('ix_admin_notes_user_id', ['user_id'])


def downgrade():
    op.drop_table('admin_notes')
