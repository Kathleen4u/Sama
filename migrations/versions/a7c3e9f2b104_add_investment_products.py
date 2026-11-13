"""Add investment products, positions and lots

Revision ID: a7c3e9f2b104
Revises: d03f50b624eb
Create Date: 2026-10-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c3e9f2b104'
down_revision = 'd03f50b624eb'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('investment_products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('product_type', sa.String(length=20), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('risk_level', sa.String(length=30), nullable=False),
    sa.Column('min_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('terms', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )

    op.create_table('investment_positions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('product_code', sa.String(length=40), nullable=False),
    sa.Column('product_type', sa.String(length=20), nullable=False),
    sa.Column('product_name', sa.String(length=100), nullable=False),
    sa.Column('portfolio_code', sa.String(length=40), nullable=True),
    sa.Column('principal', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('rate', sa.Numeric(precision=6, scale=3), nullable=True),
    sa.Column('term_days', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accrual_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('matures_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('realized_gain', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('payout_amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('investment_positions', schema=None) as batch_op:
        batch_op.create_index('ix_investment_positions_user_status', ['user_id', 'status'], unique=False)
        batch_op.create_index('ix_investment_positions_status_matures', ['status', 'matures_at'], unique=False)

    op.create_table('investment_lots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('position_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=20), nullable=False),
    sa.Column('shares', sa.Numeric(precision=20, scale=8), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('cost', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['position_id'], ['investment_positions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('investment_lots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_investment_lots_position_id'), ['position_id'], unique=False)


def downgrade():
    with op.batch_alter_table('investment_lots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_investment_lots_position_id'))
    op.drop_table('investment_lots')

    with op.batch_alter_table('investment_positions', schema=None) as batch_op:
        batch_op.drop_index('ix_investment_positions_status_matures')
        batch_op.drop_index('ix_investment_positions_user_status')
    op.drop_table('investment_positions')

    op.drop_table('investment_products')
