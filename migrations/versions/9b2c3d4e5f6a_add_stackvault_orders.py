"""add stackvault orders

Revision ID: 9b2c3d4e5f6a
Revises: 8a1b2c3d4e5f
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b2c3d4e5f6a"
down_revision: Union[str, None] = "8a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stackvault_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("goods_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("order_id", sa.String(length=128), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["goods_id"], ["goods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_stackvault_orders_user_id", "stackvault_orders", ["user_id"])
    op.create_index("ix_stackvault_orders_goods_id", "stackvault_orders", ["goods_id"])


def downgrade() -> None:
    op.drop_index("ix_stackvault_orders_goods_id", table_name="stackvault_orders")
    op.drop_index("ix_stackvault_orders_user_id", table_name="stackvault_orders")
    op.drop_table("stackvault_orders")
