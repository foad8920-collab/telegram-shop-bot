"""add StackVault pricing settings and price overrides

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stackvault_pricing_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "default_markup_percentage",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default=sa.text("30.00"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "stackvault_price_overrides",
        sa.Column("product_id", sa.String(length=100), primary_key=True),
        sa.Column("manual_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("stackvault_price_overrides")
    op.drop_table("stackvault_pricing_settings")
