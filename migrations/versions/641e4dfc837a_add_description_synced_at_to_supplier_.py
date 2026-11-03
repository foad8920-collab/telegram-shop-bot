"""add description_synced_at to supplier_products

Revision ID: 641e4dfc837a
Revises: 1e3109d04156
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '641e4dfc837a'
down_revision = '1e3109d04156'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    cols = [c['name'] for c in inspect(bind).get_columns('supplier_products')]
    if 'description_synced_at' not in cols:
        op.add_column('supplier_products', sa.Column('description_synced_at', sa.DateTime(timezone=True), nullable=True))

def downgrade():
    bind = op.get_bind()
    cols = [c['name'] for c in inspect(bind).get_columns('supplier_products')]
    if 'description_synced_at' in cols:
        op.drop_column('supplier_products', 'description_synced_at')
