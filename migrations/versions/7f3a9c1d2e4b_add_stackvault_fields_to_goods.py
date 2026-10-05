"""add StackVault supplier fields to goods

Revision ID: 7f3a9c1d2e4b
Revises: 24a5b76d4ac0
Create Date: 2026-10-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f3a9c1d2e4b"
down_revision: Union[str, None] = "24a5b76d4ac0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("goods", sa.Column("stackvault_product_id", sa.String(length=100), nullable=True))
    op.add_column("goods", sa.Column("supplier_price", sa.Numeric(12, 2), nullable=True))
    op.add_column("goods", sa.Column("supplier_stock", sa.Integer(), nullable=True))
    op.add_column("goods", sa.Column("supplier_in_stock", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("goods", "supplier_in_stock")
    op.drop_column("goods", "supplier_stock")
    op.drop_column("goods", "supplier_price")
    op.drop_column("goods", "stackvault_product_id")
