"""add_show_in_local_to_supplier_products

Revision ID: 1e3109d04156
Revises: 0bdbe2b0f45f
Create Date: 2026-11-02 15:41:50.510527

Adds show_in_local flag to supplier_products so a supplier product can be
displayed exclusively in local subscriptions while staying linked to its
supplier for price/stock sync. Default False, existing rows stay False.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = '1e3109d04156'
down_revision: Union[str, None] = '0bdbe2b0f45f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'supplier_products' not in inspector.get_table_names():
        return
    cols = [c['name'] for c in inspector.get_columns('supplier_products')]
    if 'show_in_local' not in cols:
        op.add_column(
            'supplier_products',
            sa.Column('show_in_local', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if 'supplier_products' not in inspector.get_table_names():
        return
    cols = [c['name'] for c in inspector.get_columns('supplier_products')]
    if 'show_in_local' in cols:
        op.drop_column('supplier_products', 'show_in_local')
