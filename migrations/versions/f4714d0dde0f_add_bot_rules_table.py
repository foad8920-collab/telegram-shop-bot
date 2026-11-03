"""add bot_rules table

Revision ID: f4714d0dde0f
Revises: 641e4dfc837a
"""
from alembic import op
import sqlalchemy as sa


revision = 'f4714d0dde0f'
down_revision = '641e4dfc837a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'bot_rules' not in inspector.get_table_names():
        op.create_table('bot_rules',
            sa.Column('id', sa.BigInteger(), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('sort_order', sa.BigInteger(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'bot_rules' in inspector.get_table_names():
        op.drop_table('bot_rules')