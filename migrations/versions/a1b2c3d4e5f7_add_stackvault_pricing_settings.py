"""add StackVault pricing settings to goods

Revision ID: a1b2c3d4e5f7
Revises: 9b2c3d4e5f6a
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "9b2c3d4e5f6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("goods", sa.Column("stackvault_pricing_mode", sa.String(length=16), nullable=True))
    op.add_column("goods", sa.Column("stackvault_pricing_value", sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column("goods", sa.Column("stackvault_enabled", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("goods", "stackvault_enabled")
    op.drop_column("goods", "stackvault_pricing_value")
    op.drop_column("goods", "stackvault_pricing_mode")
