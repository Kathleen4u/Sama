"""add referral system

Revision ID: 88a582ac9387
Revises: 59f9f19ab23d
Create Date: 2026-03-28 16:22:20.388952

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '88a582ac9387'
down_revision = '59f9f19ab23d'
branch_labels = None
depends_on = None


def upgrade():
    # referrals table already exists in local DB (created via db.create_all previously)
    # Only create it if missing — safe for both local SQLite and production PostgreSQL
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'referrals' not in existing_tables:
        op.create_table(
            'referrals',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('referrer_id', sa.Integer(), nullable=False),
            sa.Column('referred_id', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('reward_amount', sa.Numeric(precision=12, scale=2), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['referred_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('referred_id', name='uq_referral_referred_id')
        )
        with op.batch_alter_table('referrals', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_referrals_referred_id'), ['referred_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_referrals_referrer_id'), ['referrer_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_referrals_status'), ['status'], unique=False)

    # Add new columns to users only if they don't already exist
    existing_user_columns = [col['name'] for col in inspector.get_columns('users')]

    with op.batch_alter_table('users', schema=None) as batch_op:
        if 'referral_code' not in existing_user_columns:
            batch_op.add_column(sa.Column(
                'referral_code',
                sa.String(length=20),
                nullable=True
            ))
            batch_op.create_index(
                batch_op.f('ix_users_referral_code'),
                ['referral_code'],
                unique=True
            )
        if 'rewards_balance' not in existing_user_columns:
            batch_op.add_column(sa.Column(
                'rewards_balance',
                sa.Numeric(precision=12, scale=2),
                nullable=False,
                server_default='0.00'
            ))
        batch_op.alter_column('is_admin',
                              existing_type=sa.BOOLEAN(),
                              nullable=True)


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_referral_code'))
        batch_op.drop_column('rewards_balance')
        batch_op.drop_column('referral_code')
        batch_op.alter_column('is_admin',
                              existing_type=sa.BOOLEAN(),
                              nullable=True)

    with op.batch_alter_table('referrals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_referrals_status'))
        batch_op.drop_index(batch_op.f('ix_referrals_referrer_id'))
        batch_op.drop_index(batch_op.f('ix_referrals_referred_id'))

    op.drop_table('referrals')