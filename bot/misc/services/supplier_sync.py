"""Supplier sync service  fetch products from external APIs."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy import select, update

from bot.database.main import Database
from bot.database.models.main import (
    Supplier, SupplierProduct, SupplierPricingRule,
    SupplierCategoryMarkup, SupplierPriceOverride,
    SupplierSyncLog,
)


logger = logging.getLogger(__name__)


# ============================================================
#  Helpers
# ============================================================

def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Best-effort convert any value to Decimal."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _extract_category(product: dict) -> Optional[str]:
    """Extract a category name from various formats."""
    cat = product.get("category")
    if isinstance(cat, dict):
        return str(cat.get("name") or cat.get("id") or "").strip() or None
    if isinstance(cat, str) and cat.strip():
        return cat.strip()
    name = product.get("category_name")
    if name:
        return str(name).strip()
    cid = product.get("category_id")
    if cid is not None:
        return str(cid)
    return None


def _extract_cost_price(product: dict) -> Decimal:
    """Extract cost price from various field names (supports Pandora's unit_price)."""
    for key in ("price", "supplier_price", "cost_price", "cost", "unit_price", "base_price"):
        if key in product and product[key] is not None:
            return _to_decimal(product[key])
    return Decimal("0")


def _extract_stock(product: dict) -> int:
    """Extract stock from various field names (Pandora uses available_stock)."""
    for key in ("stock", "available_stock", "quantity", "count", "remaining"):
        if key in product and product[key] is not None:
            try:
                return int(product[key])
            except (ValueError, TypeError):
                continue
    return 0


def _is_unlimited_stock(product: dict) -> bool:
    """Best-effort check for unlimited stock."""
    if product.get("is_unlimited") or product.get("unlimited"):
        return True
    stock = _extract_stock(product)
    # Pandora: very large stock  treat as unlimited
    if stock >= 999999:
        return True
    return False


def _compute_selling_price(
    cost_price: Decimal,
    category: Optional[str],
    rule: Optional[SupplierPricingRule],
    category_markups: dict[str, Decimal],
    manual_price: Optional[Decimal] = None,
    fixed_profit: Optional[Decimal] = None,
    category_fixed_profits: Optional[dict[str, Decimal]] = None,
) -> Decimal:
    """Compute selling price from cost + pricing rules.

    Priority:
      1. manual_price (if > 0)               absolute final price
      2. fixed_profit (if > 0)               cost + fixed profit
      3. category fixed_profit (if > 0)      cost + category fixed profit
      4. markup_percent (rule/category)      cost * (1 + percent/100) + markup_fixed
    """
    # Priority 1: manual final price
    if manual_price is not None and manual_price > 0:
        return manual_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Defaults
    percent = Decimal("30")
    fixed = Decimal("0")
    min_margin = Decimal("5")
    round_to = Decimal("0.01")
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None

    if rule is not None:
        percent = _to_decimal(rule.markup_percent, Decimal("30"))
        fixed = _to_decimal(rule.markup_fixed, Decimal("0"))
        min_margin = _to_decimal(rule.min_margin_percent, Decimal("5"))
        round_to = _to_decimal(rule.round_to, Decimal("0.01")) or Decimal("0.01")
        min_price = _to_decimal(rule.min_selling_price) if rule.min_selling_price else None
        max_price = _to_decimal(rule.max_selling_price) if rule.max_selling_price else None

    # Category overrides
    cat_percent_override: Optional[Decimal] = None
    cat_fixed_override: Optional[Decimal] = None

    if category:
        cat_key = category.strip().lower()
        for key, cat_percent in category_markups.items():
            if key in cat_key or cat_key in key:
                cat_percent_override = cat_percent
                break
        if category_fixed_profits:
            for key, cat_fp in category_fixed_profits.items():
                if key in cat_key or cat_key in key:
                    cat_fixed_override = cat_fp
                    break

    # Priority 2: per-product fixed_profit
    if fixed_profit is not None and fixed_profit > 0:
        selling = cost_price + fixed_profit
    # Priority 3: category fixed_profit
    elif cat_fixed_override is not None and cat_fixed_override > 0:
        selling = cost_price + cat_fixed_override
    # Priority 4: percent-based
    else:
        if cat_percent_override is not None:
            percent = cat_percent_override
        selling = cost_price * (Decimal("1") + percent / Decimal("100")) + fixed

    # Min margin guard
    min_allowed = cost_price * (Decimal("1") + min_margin / Decimal("100"))
    if selling < min_allowed:
        selling = min_allowed

    # Min/Max bounds
    if min_price is not None and selling < min_price:
        selling = min_price
    if max_price is not None and selling > max_price:
        selling = max_price

    # Round
    if round_to > 0:
        steps = (selling / round_to).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        selling = (steps * round_to)

    return selling.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ============================================================
#  Main sync
# ============================================================

async def sync_supplier(supplier_id: int, *, manual: bool = False) -> dict:
    """Sync all products of a supplier from its external API.

    Returns a report dict with keys:
        status, added, updated, removed, total, error
    """
    report = {
        "status": "unknown",
        "added": 0,
        "updated": 0,
        "removed": 0,
        "total": 0,
        "error": None,
    }

    # ---- Load supplier ----
    async with Database().session() as session:
        supplier = await session.get(Supplier, supplier_id)
        if supplier is None:
            report["status"] = "failed"
            report["error"] = f"Supplier {supplier_id} not found"
            return report

        api_url = (supplier.api_url or "").strip()

        # Skip local suppliers
        if api_url.startswith("local://"):
            report["status"] = "skipped"
            report["error"] = "Local supplier (no API)"
            return report

    # ---- Fetch products ----
    try:
        products = await _fetch_products(supplier)
    except Exception as e:
        logger.exception("Failed to fetch products for supplier %s: %s", supplier_id, e)
        report["status"] = "failed"
        report["error"] = str(e)
        # Record failed sync
        await _record_sync_failure(supplier_id, e)
        return report

    if not products:
        report["status"] = "success"
        report["error"] = "No products returned"
        await _record_sync_success(supplier_id, report)
        return report

    report["total"] = len(products)

    # ---- Load pricing rules ----
    async with Database().session() as session:
        rule = (await session.execute(
            select(SupplierPricingRule).where(SupplierPricingRule.supplier_id == supplier_id)
        )).scalar_one_or_none()

        cat_rows = (await session.execute(
            select(SupplierCategoryMarkup).where(SupplierCategoryMarkup.supplier_id == supplier_id)
        )).scalars().all()
        category_markups = {
            str(r.category_keyword).strip().lower(): _to_decimal(r.markup_percent)
            for r in cat_rows
        }

        override_rows = (await session.execute(
            select(SupplierPriceOverride).where(SupplierPriceOverride.supplier_id == supplier_id)
        )).scalars().all()
        overrides = {str(r.external_id): _to_decimal(r.fixed_price) for r in override_rows}

        # Category fixed profits
        cat_fp_rows = (await session.execute(
            select(SupplierCategoryMarkup).where(SupplierCategoryMarkup.supplier_id == supplier_id)
        )).scalars().all()
        category_fixed_profits = {
            str(r.category_keyword).strip().lower(): _to_decimal(r.fixed_profit)
            for r in cat_fp_rows
            if getattr(r, "fixed_profit", None) is not None
        }

    # ---- Process products ----
    now = datetime.now(timezone.utc)
    seen_external_ids: set[str] = set()

    async with Database().session() as session:
        # Preload existing products for this supplier
        existing_rows = (await session.execute(
            select(SupplierProduct).where(SupplierProduct.supplier_id == supplier_id)
        )).scalars().all()
        existing_by_ext: dict[str, SupplierProduct] = {
            str(p.external_id): p for p in existing_rows
        }

        for product in products:
            ext_id_raw = product.get("id") or product.get("external_id")
            if ext_id_raw is None:
                continue
            ext_id = str(ext_id_raw)
            if not ext_id or ext_id in seen_external_ids:
                continue
            seen_external_ids.add(ext_id)

            name = str(product.get("name") or "").strip() or f"Product {ext_id}"
            description = product.get("description") or product.get("short_description")
            category = _extract_category(product)
            cost_price = _extract_cost_price(product)

            stock = _extract_stock(product)
            is_unlimited = _is_unlimited_stock(product)
            in_stock = product.get("in_stock")
            if in_stock is None:
                # Fallback: if no in_stock field, infer from stock
                is_available = stock > 0 or is_unlimited
            else:
                is_available = bool(in_stock)

            # Lookup existing product FIRST
            existing = existing_by_ext.get(ext_id)

            # ---- Manual price ----
            # Priority:
            #   1. Fixed override from DB (SupplierPriceOverride) - applies to all
            #   2. manual_price from existing DB product - user-set
            #   3. None
            if ext_id in overrides:
                manual_price = overrides[ext_id]
            elif existing is not None and existing.manual_price is not None and existing.manual_price > 0:
                manual_price = _to_decimal(existing.manual_price)
            else:
                manual_price = None

            # ---- Fixed profit ----
            # Priority:
            #   1. existing.fixed_profit (user-set) - preserved across syncs
            #   2. default_fixed_profit from rule (if any)
            #   3. 0
            if existing is not None and existing.fixed_profit is not None and existing.fixed_profit > 0:
                product_fixed_profit = _to_decimal(existing.fixed_profit)
            else:
                # Fall back to rule's default (if any)
                product_fixed_profit = Decimal("0")
                if rule is not None:
                    dfp = getattr(rule, "default_fixed_profit", None)
                    if dfp is not None:
                        try:
                            product_fixed_profit = _to_decimal(dfp)
                        except Exception:
                            pass

            # ---- Selling price ----
            selling_price = _compute_selling_price(
                cost_price,
                category,
                rule,
                category_markups,
                manual_price=manual_price,
                fixed_profit=product_fixed_profit,
                category_fixed_profits=category_fixed_profits,
            )

            if existing is not None:
                # Update
                existing.name = name
                existing.description = str(description) if description else None
                existing.category = category
                existing.cost_price = cost_price
                existing.selling_price = selling_price
                existing.manual_price = manual_price
                existing.fixed_profit = product_fixed_profit
                existing.stock = stock
                existing.is_unlimited = is_unlimited
                existing.is_available = is_available
                existing.raw_data = product
                existing.last_synced_at = now
                report["updated"] += 1
            else:
                # Insert
                sp = SupplierProduct(
                    supplier_id=supplier_id,
                    external_id=ext_id,
                    name=name,
                    description=str(description) if description else None,
                    category=category,
                    cost_price=cost_price,
                    selling_price=selling_price,
                    manual_price=manual_price,
                    fixed_profit=product_fixed_profit,
                    stock=stock,
                    is_unlimited=is_unlimited,
                    is_available=is_available,
                    raw_data=product,
                    last_synced_at=now,
                )
                session.add(sp)
                report["added"] += 1

        # Mark missing products as unavailable
        for ext_id, prod in existing_by_ext.items():
            if ext_id not in seen_external_ids:
                if prod.is_available:
                    prod.is_available = False
                    report["removed"] += 1

        # Update supplier.last_sync_*
        supplier = await session.get(Supplier, supplier_id)
        if supplier is not None:
            supplier.last_sync_at = now
            supplier.last_sync_status = "success"
            supplier.last_sync_error = None
            supplier.last_sync_product_count = report["total"]
            supplier.sync_errors_count = 0

        await session.commit()

    report["status"] = "success"
    await _record_sync_success(supplier_id, report)
    logger.info("Supplier %s sync done: %s", supplier_id, report)
    return report


# ============================================================
#  Fetch products (by supplier type)
# ============================================================

async def _fetch_products(supplier: Supplier) -> list[dict]:
    """Fetch products using the appropriate client.

    StackVault and Pandora keep their dedicated clients (env-based keys).
    Any other supplier falls back to the generic client (DB-based key).
    """
    name_lower = (supplier.name or "").lower()
    api_url = (supplier.api_url or "").strip()
    api_key = (supplier.api_key or "").strip()

    # --- Known suppliers ---
    if "stackvault" in name_lower:
        from bot.misc.services import StackVaultClient
        async with StackVaultClient() as client:
            return await client.get_products()

    if "pandora" in name_lower:
        from bot.misc.services.pandora import PandoraClient
        async with PandoraClient() as client:
            return await client.get_products()

    # --- Generic fallback ---
    from bot.misc.services.generic_supplier import (
        GenericSupplierClient,
        GenericSupplierError,
    )
    try:
        async with GenericSupplierClient(api_url, api_key) as client:
            return await client.get_products()
    except GenericSupplierError as e:
        raise ValueError(f"Generic supplier failed: {e}") from e


# ============================================================
#  Sync logging
async def _record_sync_success(supplier_id: int, report: dict) -> None:
    """Insert a supplier_sync_logs row (success)."""
    try:
        async with Database().session() as session:
            log = SupplierSyncLog(
                supplier_id=supplier_id,
                finished_at=datetime.now(timezone.utc),
                status="success",
                products_total=report.get("total", 0),
                products_added=report.get("added", 0),
                products_updated=report.get("updated", 0),
                products_removed=report.get("removed", 0),
                price_changes=0,
                details={
                    "manual": report.get("manual", False),
                    "error": report.get("error"),
                },
            )
            session.add(log)
            await session.commit()
    except Exception as e:
        logger.warning("Failed to record sync log: %s", e)


async def _record_sync_failure(supplier_id: int, error: Exception) -> None:
    """Insert a supplier_sync_logs row (failure) + update supplier."""
    try:
        async with Database().session() as session:
            log = SupplierSyncLog(
                supplier_id=supplier_id,
                finished_at=datetime.now(timezone.utc),
                status="failed",
                error_message=str(error)[:1000],
            )
            session.add(log)

            supplier = await session.get(Supplier, supplier_id)
            if supplier is not None:
                supplier.last_sync_at = datetime.now(timezone.utc)
                supplier.last_sync_status = "failed"
                supplier.last_sync_error = str(error)[:1000]
                supplier.sync_errors_count = (supplier.sync_errors_count or 0) + 1

            await session.commit()
    except Exception as e:
        logger.warning("Failed to record sync failure: %s", e)
