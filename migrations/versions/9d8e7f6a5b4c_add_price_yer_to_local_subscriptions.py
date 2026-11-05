"""add price_yer to local_subscriptions

Revision ID: 9d8e7f6a5b4c
Revises: f4714d0dde0f
Create Date: 2026-11-05

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d8e7f6a5b4c'
down_revision = 'f4714d0dde0f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('local_subscriptions', sa.Column('price_yer', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('local_subscriptions', 'price_yer')