"""Add admin audit log and transaction flag columns

Revision ID: f7a3d2c9e1b4
Revises: e5b2c8a1d9f3
Create Date: 2026-10-10 12:00:00.000000

Flags used to be stored as a "[FLAGGED] ..." prefix on the description,
which users can see in their history. Existing prefixed rows are moved into
the new columns and their description restored:

  "[FLAGGED] USDT — reversed: refunded"  ->  description "USDT",
                                             flag_reason "reversed: refunded"
  "[FLAGGED] USDT"                       ->  description "USDT",
                                             flag_reason "Flagged before flag reasons were recorded"
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7a3d2c9e1b4'
down_revision = 'e5b2c8a1d9f3'
branch_labels = None
depends_on = None

PREFIX = "[FLAGGED]"
SEPARATOR = " — "
LEGACY_REASON = "Flagged before flag reasons were recorded"


def upgrade():
    op.create_table(
        'admin_audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('target_type', sa.String(length=30), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['admin_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('admin_audit_logs', schema=None) as batch_op:
        batch_op.create_index('ix_admin_audit_logs_admin_id', ['admin_id'])
        batch_op.create_index('ix_admin_audit_logs_action', ['action'])
        batch_op.create_index('ix_admin_audit_logs_created_at', ['created_at'])
        batch_op.create_index('idx_audit_target', ['target_type', 'target_id'])

    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_flagged', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('flag_reason', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('flagged_by', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('flagged_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key('fk_transactions_flagged_by_users', 'users', ['flagged_by'], ['id'])
        batch_op.create_index('ix_transactions_is_flagged', ['is_flagged'])

    # Move "[FLAGGED] ..." descriptions into the new columns
    conn = op.get_bind()
    transactions = sa.table(
        'transactions',
        sa.column('id', sa.Integer),
        sa.column('description', sa.String),
        sa.column('is_flagged', sa.Boolean),
        sa.column('flag_reason', sa.String),
        sa.column('flagged_at', sa.DateTime(timezone=True)),
    )
    rows = conn.execute(
        sa.select(transactions.c.id, transactions.c.description)
        .where(transactions.c.description.like(f"{PREFIX}%"))
    ).all()
    now = datetime.now(timezone.utc)
    for row in rows:
        rest = row.description[len(PREFIX):].strip()
        original, sep, reason = rest.partition(SEPARATOR)
        conn.execute(
            transactions.update()
            .where(transactions.c.id == row.id)
            .values(
                description=original.strip(),
                is_flagged=True,
                flag_reason=(reason.strip() if sep else "") or LEGACY_REASON,
                flagged_at=now,
            )
        )


def downgrade():
    # Put the visible prefix back so flags aren't lost on downgrade
    conn = op.get_bind()
    transactions = sa.table(
        'transactions',
        sa.column('id', sa.Integer),
        sa.column('description', sa.String),
        sa.column('is_flagged', sa.Boolean),
        sa.column('flag_reason', sa.String),
    )
    rows = conn.execute(
        sa.select(transactions.c.id, transactions.c.description, transactions.c.flag_reason)
        .where(transactions.c.is_flagged == sa.true())
    ).all()
    for row in rows:
        description = f"{PREFIX} {row.description}"
        if row.flag_reason and row.flag_reason != LEGACY_REASON:
            description += f"{SEPARATOR}{row.flag_reason}"
        conn.execute(
            transactions.update()
            .where(transactions.c.id == row.id)
            .values(description=description[:255])
        )

    with op.batch_alter_table('transactions', schema=None) as batch_op:
        batch_op.drop_index('ix_transactions_is_flagged')
        batch_op.drop_constraint('fk_transactions_flagged_by_users', type_='foreignkey')
        batch_op.drop_column('flagged_at')
        batch_op.drop_column('flagged_by')
        batch_op.drop_column('flag_reason')
        batch_op.drop_column('is_flagged')

    op.drop_table('admin_audit_logs')
