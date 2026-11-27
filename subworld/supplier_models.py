"""SubWorld supplier tables (same shape as the Yemen bot, separate database).

Table names match the Yemen bot on purpose: the databases are separate,
so there is no clash, and the sync logic stays close to the original.
Default profit margin is 30% (user decision 2026-11-27).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .db import Base

DEFAULT_MARGIN = 30


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_url: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key: Mapped[str] = mapped_column(String(255), nullable=False)
    profit_margin: Mapped[float] = mapped_column(Numeric(5, 2), default=DEFAULT_MARGIN)
    style: Mapped[str | None] = mapped_column(String(20), nullable=True, default="primary")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    icon_emoji: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=240, server_default="240")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_product_count: Mapped[int | None] = mapped_column(Integer, default=0)
    sync_errors_count: Mapped[int | None] = mapped_column(Integer, default=0)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    def __str__(self):
        return self.display_name or self.name or ""


class SupplierProduct(Base):
    """Cached product from an external supplier (StackVault/Pandora/...)."""

    __tablename__ = "supplier_products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "external_id", name="uq_sp_supplier_external"),
        Index("idx_sp_supplier_visible", "supplier_id", "is_visible", "is_available"),
        Index("idx_sp_synced", "last_synced_at"),
        Index("idx_sp_category", "supplier_id", "category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    cost_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    selling_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    manual_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    fixed_profit: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0",
    )
    currency: Mapped[str | None] = mapped_column(String(10), default="USD")

    stock: Mapped[int | None] = mapped_column(Integer, default=0)
    is_unlimited: Mapped[bool] = mapped_column(Boolean, default=False)

    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    show_in_local: Mapped[bool] = mapped_column(Boolean, default=False)

    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    def __str__(self):
        return self.name or ""


class SupplierPricingRule(Base):
    """Global pricing rules for a supplier (percent/fixed markup)."""

    __tablename__ = "supplier_pricing_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, unique=True,
    )

    markup_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=DEFAULT_MARGIN)
    markup_fixed: Mapped[float | None] = mapped_column(Numeric(10, 2), default=0)
    min_margin_percent: Mapped[float | None] = mapped_column(Numeric(5, 2), default=5)
    default_fixed_profit: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0",
    )
    min_selling_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    max_selling_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    round_to: Mapped[float | None] = mapped_column(Numeric(5, 2), default=0.01)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    def __str__(self):
        return f"Pricing[{self.supplier_id}] {self.markup_percent}%"


class SupplierCategoryMarkup(Base):
    """Category-specific markup override for a supplier."""

    __tablename__ = "supplier_category_markups"
    __table_args__ = (
        UniqueConstraint("supplier_id", "category_keyword", name="uq_scm_supplier_category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False,
    )
    category_keyword: Mapped[str] = mapped_column(String(100), nullable=False)
    markup_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    fixed_profit: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=0, server_default="0",
    )

    def __str__(self):
        return f"{self.category_keyword}: +{self.markup_percent}%"


class SupplierPriceOverride(Base):
    """A fixed price override for a specific product."""

    __tablename__ = "supplier_price_overrides"
    __table_args__ = (
        UniqueConstraint("supplier_id", "external_id", name="uq_spo_supplier_external"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    fixed_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())

    def __str__(self):
        return f"Override[{self.supplier_id}:{self.external_id}] -> {self.fixed_price}"


class SupplierSyncLog(Base):
    """Log entry for each sync operation."""

    __tablename__ = "supplier_sync_logs"
    __table_args__ = (
        Index("idx_ssl_supplier", "supplier_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    products_total: Mapped[int | None] = mapped_column(Integer, default=0)
    products_added: Mapped[int | None] = mapped_column(Integer, default=0)
    products_updated: Mapped[int | None] = mapped_column(Integer, default=0)
    products_removed: Mapped[int | None] = mapped_column(Integer, default=0)
    price_changes: Mapped[int | None] = mapped_column(Integer, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __str__(self):
        return f"Sync[{self.supplier_id}] {self.status} @ {self.started_at}"
