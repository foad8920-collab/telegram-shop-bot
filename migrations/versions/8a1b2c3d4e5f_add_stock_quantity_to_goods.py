"""add numeric stock quantity to goods

Revision ID: 8a1b2c3d4e5f
Revises: 7f3a9c1d2e4b
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "8a1b2c3d4e5f"
down_revision: Union[str, None] = "7f3a9c1d2e4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("goods", sa.Column("stock_quantity", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("goods", "stock_quantity")
