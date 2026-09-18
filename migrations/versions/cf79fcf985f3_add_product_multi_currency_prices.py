"""add product multi currency prices

Revision ID: cf79fcf985f3
Revises: d7e8f9a0b1c2
Create Date: 2026-09-18 15:22:04.886629

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cf79fcf985f3'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    op.create_table(
        "product_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["goods.id"],
            ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "currency",
            name="uq_product_price_currency"
        ),
    )

    op.create_index(
        "ix_product_prices_item_id",
        "product_prices",
        ["item_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_prices_item_id",
        table_name="product_prices"
    )

    op.drop_table("product_prices")
