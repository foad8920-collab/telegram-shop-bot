"""SubWorld data models (separate tables from the Yemen bot)."""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now():
    return datetime.now(timezone.utc)


class Category(Base):
    __tablename__ = "subworld_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "subworld_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("subworld_categories.id"), nullable=True
    )
    name_ar: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200))
    description_ar: Mapped[str] = mapped_column(Text, default="")
    description_en: Mapped[str] = mapped_column(Text, default="")
    price_usd: Mapped[float] = mapped_column(Numeric(10, 2))
    price_sar: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Delivery, same as the Yemen bot: codes | invite | manual
    delivery_type: Mapped[str] = mapped_column(String(20), default="codes")
    codes_per_purchase: Mapped[int] = mapped_column(Integer, default=1)
    invite_field_type: Mapped[str] = mapped_column(String(20), default="email")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped["Category | None"] = relationship(back_populates="products")
    codes: Mapped[list["ProductCode"]] = relationship(back_populates="product")


class ProductCode(Base):
    """One prepaid code per row; delivery marks it used by a buyer."""

    __tablename__ = "subworld_product_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("subworld_products.id"))
    code: Mapped[str] = mapped_column(Text, unique=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_by_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    product: Mapped["Product"] = relationship(back_populates="codes")


class SubworldUser(Base):
    __tablename__ = "subworld_users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    language: Mapped[str] = mapped_column(String(2), default="ar")
    balance_usd: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )


class Order(Base):
    __tablename__ = "subworld_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("subworld_users.id"))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("subworld_products.id"), nullable=True)
    supplier_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_products.id"), nullable=True
    )
    amount_usd: Mapped[float] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    proof: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_id: Mapped[int | None] = mapped_column(
        ForeignKey("subworld_product_codes.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
