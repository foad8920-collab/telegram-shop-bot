"""SubWorld supplier sync - adapted from the Yemen bot's supplier_sync.

Same fetching, same pricing priority, same auto-hide after 2 failures.
Differences from the Yemen bot:
- Uses the SubWorld database (own session factory and models).
- Prices are USD only (no YER/SAR conversion anywhere).
- Default profit margin is 30% (user decision 2026-11-27).
- Product-update posts go to the SubWorld updates channel
  (SUBWORLD_UPDATES_CHANNEL_ID) and alerts to the SubWorld monitoring
  channel (SUBWORLD_MONITORING_CHANNEL_ID). When a channel is not set
  yet, events are only logged - nothing is posted to Yemen channels.
- The supplier API clients are imported lazily from bot.misc.services,
  but every supplier's connection comes from its Supplier row
  (api_url + api_key, edited in the admin panel - like Vente), passed
  as overrides. The clients never read the Yemen DB or env keys, so
  changing a supplier's URL/key in the panel takes effect on the next
  sync with no code or env change.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy import select

from .db import get_session_factory
from .supplier_models import (
    DEFAULT_MARGIN,
    Supplier,
    SupplierCategoryMarkup,
    SupplierPriceOverride,
    SupplierPricingRule,
    SupplierProduct,
    SupplierSyncLog,
)

logger = logging.getLogger("subworld.sync")


def _new_session():
    return get_session_factory()()


def _updates_channel_id() -> Optional[int]:
    raw = (os.environ.get("SUBWORLD_UPDATES_CHANNEL_ID") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _monitoring_channel_id() -> Optional[int]:
    raw = (os.environ.get("SUBWORLD_MONITORING_CHANNEL_ID") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


# ============================================================
#  Helpers (same as the Yemen bot)
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
    supplier_margin: Optional[Decimal] = None,
) -> Decimal:
    """Compute selling price from cost + pricing rules.

    Priority:
      1. manual_price (if > 0)               absolute final price
      2. fixed_profit (if > 0)               cost + fixed profit
      3. category fixed_profit (if > 0)      cost + category fixed profit
      4. markup_percent (rule/category)      cost * (1 + percent/100) + markup_fixed

    Default margin is 30% (user decision); a supplier profit_margin
    or a pricing rule overrides it.
    """
    # Priority 1: manual final price
    if manual_price is not None and manual_price > 0:
        return manual_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Defaults
    base_percent = supplier_margin if (supplier_margin is not None and supplier_margin > 0) else Decimal(str(DEFAULT_MARGIN))
    percent = base_percent
    fixed = Decimal("0")
    min_margin = Decimal("5")
    round_to = Decimal("0.01")
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None

    if rule is not None:
        percent = _to_decimal(rule.markup_percent, base_percent)
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

async def sync_supplier(supplier_id: int, *, manual: bool = False, bot=None) -> dict:
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
        "manual": manual,
    }

    # ---- Load supplier ----
    async with _new_session() as session:
        supplier = await session.get(Supplier, supplier_id)
        if supplier is None:
            report["status"] = "failed"
            report["error"] = f"Supplier {supplier_id} not found"
            return report
        is_stackvault = "stackvault" in (supplier.name or "").lower()
        supplier_margin = _to_decimal(getattr(supplier, "profit_margin", None))

        api_url = (supplier.api_url or "").strip()
        api_key = (supplier.api_key or "").strip()

        # Skip local suppliers
        if api_url.startswith("local://"):
            report["status"] = "skipped"
            report["error"] = "Featured subscriptions entry (no API)"
            return report

        # Panel connection required: a missing URL/key is a config error,
        # not an outage - fail without counting it as a sync failure
        # (so products are not auto-hidden for a missing config).
        if not api_url or not api_key:
            report["status"] = "failed"
            report["error"] = "API URL/key not set for this supplier in the admin panel"
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
        # Auto-hide: after 2 consecutive list-fetch failures, hide products + alert.
        await _hide_supplier_products(supplier_id, bot, str(e))
        return report

    if not products:
        # Empty list: outage if the supplier previously had products.
        _had_products = False
        try:
            async with _new_session() as _s:
                _row = (await _s.execute(
                    select(SupplierProduct.id).where(
                        SupplierProduct.supplier_id == supplier_id
                    ).limit(1)
                )).first()
                _had_products = _row is not None
        except Exception:
            _had_products = False
        if _had_products:
            _empty_err = ValueError("API returned no products while supplier had products")
            logger.warning("Empty product list for supplier %s while it had products", supplier_id)
            report["status"] = "failed"
            report["error"] = str(_empty_err)
            await _record_sync_failure(supplier_id, _empty_err)
            await _hide_supplier_products(supplier_id, bot, str(_empty_err))
            return report
        report["status"] = "success"
        report["error"] = "No products returned"
        await _record_sync_success(supplier_id, report)
        return report

    report["total"] = len(products)

    # ---- Load pricing rules ----
    async with _new_session() as session:
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
    was_failing = False
    detail_fetched_count = 0
    detail_rate_limited = False
    seen_external_ids: set[str] = set()

    async with _new_session() as session:
        # Preload existing products for this supplier
        existing_rows = (await session.execute(
            select(SupplierProduct).where(SupplierProduct.supplier_id == supplier_id)
        )).scalars().all()
        existing_by_ext: dict[str, SupplierProduct] = {
            str(p.external_id): p for p in existing_rows
        }

        events = []
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
            _old_price = existing.selling_price if existing is not None else None
            _old_stock = existing.stock if existing is not None else None
            _old_available = existing.is_available if existing is not None else None
            _old_visible = existing.is_visible if existing is not None else True

            # ---- Manual price ----
            if ext_id in overrides:
                manual_price = overrides[ext_id]
            elif existing is not None and existing.manual_price is not None and existing.manual_price > 0:
                manual_price = _to_decimal(existing.manual_price)
            else:
                manual_price = None

            # ---- Fixed profit ----
            if existing is not None and existing.fixed_profit is not None and existing.fixed_profit > 0:
                product_fixed_profit = _to_decimal(existing.fixed_profit)
            else:
                product_fixed_profit = Decimal("0")
                if rule is not None:
                    dfp = getattr(rule, "default_fixed_profit", None)
                    if dfp is not None:
                        try:
                            product_fixed_profit = _to_decimal(dfp)
                        except Exception:
                            pass

            # ---- Selling price (USD only) ----
            selling_price = _compute_selling_price(
                cost_price,
                category,
                rule,
                category_markups,
                manual_price=manual_price,
                fixed_profit=product_fixed_profit,
                category_fixed_profits=category_fixed_profits,
                supplier_margin=supplier_margin,
            )

            # ---- StackVault description detail refresh (every 6h) ----
            desc_synced_at_new = existing.description_synced_at if existing is not None else None
            if is_stackvault:
                _existing_dt = desc_synced_at_new
                _need_detail = False
                if existing is None:
                    _need_detail = True
                elif _existing_dt is None:
                    _need_detail = True
                else:
                    _dt = _existing_dt
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                    if (now - _dt).total_seconds() >= 6 * 3600:
                        _need_detail = True
                if _need_detail and not detail_rate_limited and detail_fetched_count < 10:
                    try:
                        from bot.misc.services.stackvault import StackVaultClient
                        async with StackVaultClient(api_url, api_key) as _detail_client:
                            _detail = await _detail_client.get_product_detail(ext_id)
                        _detail_desc = _detail.get("description") or _detail.get("desc") or _detail.get("details")
                        if _detail_desc:
                            description = str(_detail_desc)
                            desc_synced_at_new = now
                        detail_fetched_count += 1
                        await asyncio.sleep(1)
                    except Exception as _detail_error:
                        logger.warning("StackVault detail fetch failed for %s: %s", ext_id, _detail_error)
                        if "429" in str(_detail_error) or "Rate limit" in str(_detail_error):
                            detail_rate_limited = True
                            logger.warning("StackVault detail fetching stopped for this sync due to rate limit")
            if existing is not None:
                # Update
                existing.name = name
                if description:
                    existing.description = str(description)
                existing.description_synced_at = desc_synced_at_new
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
                if _old_visible is not False:
                    _price_changed = _old_price is not None and _old_price != selling_price
                    _stock_out = _old_stock is not None and _old_stock > 0 and stock is not None and stock <= 0
                    _stock_back = _old_stock is not None and _old_stock <= 0 and stock is not None and stock > 0
                    _stock_crossed_low = _old_stock is not None and _old_stock > 5 and stock is not None and 0 < stock <= 5
                    _stock_up = _old_stock is not None and stock is not None and stock > _old_stock
                    if _price_changed or _stock_out or _stock_back or _stock_crossed_low or _stock_up:
                        _kind = "out" if _stock_out else "back" if _stock_back else "price" if _price_changed else "low_stock" if _stock_crossed_low else "qty_up"
                        events.append({"kind": _kind, "name": name, "price_old": _old_price, "price_new": selling_price, "stock_old": _old_stock, "stock_new": stock, "available_old": _old_available, "available_new": is_available, "supplier_id": supplier_id, "product_id": existing.id})
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
                    description_synced_at=desc_synced_at_new,
                )
                session.add(sp)
                await session.flush()
                events.append({"kind": "new", "name": name, "price_old": None, "price_new": selling_price, "stock_old": None, "stock_new": stock, "available_old": False, "available_new": is_available, "supplier_id": supplier_id, "product_id": sp.id})
                report["added"] += 1

        # Mark missing products as unavailable
        for ext_id, prod in existing_by_ext.items():
            if ext_id not in seen_external_ids:
                if prod.is_available:
                    _removed_old_stock = prod.stock
                    prod.is_available = False
                    prod.stock = 0
                    if prod.is_visible is not False:
                        events.append({"kind": "out", "name": prod.name, "price_old": prod.selling_price, "price_new": prod.selling_price, "stock_old": _removed_old_stock, "stock_new": 0, "available_old": True, "available_new": False, "supplier_id": prod.supplier_id, "product_id": prod.id})
                    report["removed"] += 1

        # Update supplier.last_sync_*
        supplier = await session.get(Supplier, supplier_id)
        if supplier is not None:
            supplier.last_sync_at = now
            supplier.last_sync_status = "success"
            supplier.last_sync_error = None
            supplier.last_sync_product_count = report["total"]
            was_failing = (supplier.sync_errors_count or 0) >= 2
            supplier.sync_errors_count = 0

        await session.commit()

    if was_failing:
        await _restore_supplier_products(supplier_id, bot)

    if events:
        await _post_supplier_events(bot, events)

    report["status"] = "success"
    await _record_sync_success(supplier_id, report)
    logger.info("SubWorld supplier %s sync done: %s", supplier_id, report)
    return report


async def sync_all_suppliers(bot=None) -> list[dict]:
    """Sync every active SubWorld supplier with a real API URL."""
    reports = []
    async with _new_session() as session:
        rows = (await session.execute(
            select(Supplier).where(Supplier.is_active == True)  # noqa: E712
        )).scalars().all()
        targets = [
            (r.id, r.name)
            for r in rows
            if r.sync_enabled and not (r.api_url or "").strip().startswith("local://")
        ]
    for supplier_id, _name in targets:
        try:
            reports.append(await sync_supplier(supplier_id, bot=bot))
        except Exception as e:
            logger.exception("SubWorld sync failed for supplier %s: %s", supplier_id, e)
            reports.append({"status": "failed", "supplier_id": supplier_id, "error": str(e)})
        await asyncio.sleep(2)
    return reports


# ============================================================
#  Fetch products (by supplier type)
# ============================================================

async def _fetch_products(supplier: Supplier) -> list[dict]:
    """Fetch products using the appropriate client.

    StackVault and Pandora keep their dedicated clients, but all three
    are built from the Supplier row's api_url/api_key (panel values).
    Clients are imported lazily from bot.misc.services (same process).
    """
    name_lower = (supplier.name or "").lower()
    api_url = (supplier.api_url or "").strip()
    api_key = (supplier.api_key or "").strip()

    # --- Known suppliers ---
    # The row's api_url/api_key are passed as overrides, so the clients
    # use the panel connection and never fall back to the Yemen DB/env.
    if "stackvault" in name_lower:
        from bot.misc.services import StackVaultClient
        async with StackVaultClient(api_url, api_key) as client:
            return await client.get_products()

    if "pandora" in name_lower:
        from bot.misc.services.pandora import PandoraClient
        async with PandoraClient(api_url, api_key) as client:
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
#  Updates posts + monitoring alerts (SubWorld channels)
# ============================================================

def _format_update_text(event: dict) -> str:
    kind = event.get("kind")
    name = event.get("name")
    price_new = event.get("price_new")
    price_old = event.get("price_old")
    stock_new = event.get("stock_new")
    if kind == "new":
        title = "🆕 منتج جديد"
    elif kind == "price":
        title = "💲 تغير السعر"
    elif kind == "out":
        title = "⛔ نفد المخزون"
    elif kind == "back":
        title = "✅ عاد المخزون"
    elif kind == "low_stock":
        title = "⚠️ مخزون قليل"
    else:
        title = "📦 تحديث منتج"
    lines = [title, f"المنتج: {name}"]
    if price_old is not None and price_new is not None and price_old != price_new:
        lines.append(f"السعر: ${price_old} ← ${price_new}")
    elif price_new is not None:
        lines.append(f"السعر: ${price_new}")
    if stock_new is not None:
        lines.append(f"المخزون: {stock_new}")
    return "\n".join(lines)


async def _post_supplier_events(bot, events: list[dict]) -> None:
    """Post sync events to the SubWorld updates channel, or log them."""
    channel_id = _updates_channel_id()
    for event in events:
        text = _format_update_text(event)
        if bot is None or channel_id is None:
            logger.info("SubWorld update (no channel configured): %s", text.replace("\n", " | "))
            continue
        try:
            await bot.send_message(channel_id, text)
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning("SubWorld updates post failed: %s", e)


def _supplier_local_time() -> tuple:
    """Return (time, date) strings in UTC+3, 12-hour, Arabic."""
    now = datetime.now(timezone.utc) + timedelta(hours=3)
    hour12 = now.hour % 12 or 12
    suffix = "صباحا" if now.hour < 12 else "مساء"
    time_s = f"{hour12}:{now.minute:02d}:{now.second:02d} {suffix}"
    date_s = now.strftime("%d/%m/%Y")
    return time_s, date_s


async def _send_supplier_monitoring_alert(bot, text: str) -> bool:
    """Send a supplier alert to the SubWorld monitoring channel. Never raises."""
    channel_id = _monitoring_channel_id()
    if bot is None or channel_id is None:
        logger.warning("SubWorld monitoring alert (no channel configured): %s", text.replace("\n", " | "))
        return False
    try:
        await bot.send_message(channel_id, text)
        return True
    except Exception as e:
        logger.warning("SubWorld monitoring alert failed: %s", e)
        return False


async def _hide_supplier_products(supplier_id: int, bot, error_text: str) -> int:
    """Hide a stopped supplier's visible products after 2 consecutive failures.

    Only runs when sync_errors_count >= 2. Alerts the monitoring channel when
    the supplier first reaches 2 failures, or when products were actually hidden.
    Returns the number of products hidden.
    """
    try:
        async with _new_session() as session:
            supplier = await session.get(Supplier, supplier_id)
            if supplier is None:
                return 0
            err_count = supplier.sync_errors_count or 0
            if err_count < 2:
                return 0
            name = supplier.name or f"Supplier {supplier_id}"
            rows = (await session.execute(
                select(SupplierProduct).where(
                    SupplierProduct.supplier_id == supplier_id,
                    SupplierProduct.is_visible == True,  # noqa: E712
                )
            )).scalars().all()
            for p in rows:
                p.is_visible = False
            hidden = len(rows)
            await session.commit()
    except Exception as e:
        logger.warning("Failed to hide SubWorld supplier %s products: %s", supplier_id, e)
        return 0
    if hidden > 0 or err_count == 2:
        time_s, date_s = _supplier_local_time()
        text = chr(10).join([
            "⚠️ توقف مورد عن العمل",
            f"المورد: {name} (ID {supplier_id})",
            f"الحالة: فشلت المزامنة مرتين متتاليتين وتم إخفاء {hidden} منتج من المتجر تلقائيا",
            f"آخر خطأ: {error_text}",
            f"🕐 الوقت: {time_s}",
            f"📅 التاريخ: {date_s}",
        ])
        await _send_supplier_monitoring_alert(bot, text)
    return hidden


async def _restore_supplier_products(supplier_id: int, bot) -> int:
    """Restore a recovered supplier's hidden products and alert. Returns count."""
    try:
        async with _new_session() as session:
            supplier = await session.get(Supplier, supplier_id)
            if supplier is None:
                return 0
            name = supplier.name or f"Supplier {supplier_id}"
            rows = (await session.execute(
                select(SupplierProduct).where(
                    SupplierProduct.supplier_id == supplier_id,
                    SupplierProduct.is_visible == False,  # noqa: E712
                )
            )).scalars().all()
            for p in rows:
                p.is_visible = True
            restored = len(rows)
            await session.commit()
    except Exception as e:
        logger.warning("Failed to restore SubWorld supplier %s products: %s", supplier_id, e)
        return 0
    if restored > 0:
        time_s, date_s = _supplier_local_time()
        text = chr(10).join([
            "✅ عاد مورد للعمل",
            f"المورد: {name} (ID {supplier_id})",
            f"الحالة: نجحت المزامنة وتمت إعادة إظهار {restored} منتج في المتجر",
            f"🕐 الوقت: {time_s}",
            f"📅 التاريخ: {date_s}",
        ])
        await _send_supplier_monitoring_alert(bot, text)
    return restored


async def _record_sync_success(supplier_id: int, report: dict) -> None:
    """Insert a supplier_sync_logs row (success)."""
    try:
        async with _new_session() as session:
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
        logger.warning("Failed to record SubWorld sync log: %s", e)


async def _record_sync_failure(supplier_id: int, error: Exception) -> None:
    """Insert a supplier_sync_logs row (failure) + update supplier."""
    try:
        async with _new_session() as session:
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
        logger.warning("Failed to record SubWorld sync failure: %s", e)


# ============================================================
#  One-time supplier seeding (from the Yemen bot database)
# ============================================================

async def ensure_suppliers_seeded() -> int:
    """Copy supplier configs from the Yemen bot DB on first run only.

    Runs only when the SubWorld suppliers table is empty. Profit margin is
    forced to 30% for every supplier (user decision), whatever the Yemen
    bot has. Pricing rules default to 30% markup. Never logs secrets.
    Returns the number of suppliers seeded (0 if already seeded or skipped).
    """
    try:
        async with _new_session() as session:
            existing = (await session.execute(select(Supplier.id).limit(1))).first()
            if existing is not None:
                return 0

        from bot.database.main import Database as YemenDatabase
        from bot.database.models.main import Supplier as YemenSupplier

        copied = 0
        async with YemenDatabase().session() as ysession:
            rows = (await ysession.execute(select(YemenSupplier))).scalars().all()
            rows_data = [
                {
                    "id": r.id,
                    "name": r.name,
                    "api_url": r.api_url,
                    "api_key": r.api_key,
                    "style": r.style,
                    "is_active": r.is_active,
                    "display_name": r.display_name,
                    "icon_emoji": r.icon_emoji,
                    "sort_order": r.sort_order or 0,
                    "sync_enabled": r.sync_enabled,
                    "sync_interval_minutes": r.sync_interval_minutes or 240,
                }
                for r in rows
            ]

        if not rows_data:
            logger.warning("SubWorld supplier seeding skipped: no suppliers in Yemen DB")
            return 0

        async with _new_session() as session:
            for data in rows_data:
                # The local entry is called "featured subscriptions" in SubWorld.
                is_local_entry = (data["api_url"] or "").strip().startswith("local://")
                sup_name = "الاشتراكات المميزة" if is_local_entry else data["name"]
                session.add(Supplier(
                    id=data["id"],
                    name=sup_name,
                    api_url=data["api_url"],
                    api_key=data["api_key"],
                    profit_margin=Decimal(str(DEFAULT_MARGIN)),
                    style=data["style"],
                    is_active=data["is_active"],
                    display_name=sup_name if is_local_entry else data["display_name"],
                    icon_emoji=data["icon_emoji"],
                    sort_order=data["sort_order"],
                    sync_enabled=data["sync_enabled"],
                    sync_interval_minutes=data["sync_interval_minutes"],
                ))
                session.add(SupplierPricingRule(
                    supplier_id=data["id"],
                    markup_percent=Decimal(str(DEFAULT_MARGIN)),
                    markup_fixed=Decimal("0"),
                    min_margin_percent=Decimal("5"),
                    default_fixed_profit=Decimal("0"),
                    round_to=Decimal("0.01"),
                ))
            await session.commit()
            copied = len(rows_data)
        logger.info("SubWorld suppliers seeded: %s suppliers (margin 30%%)", copied)
        return copied
    except Exception as e:
        logger.warning("SubWorld supplier seeding failed: %s", e)
        return 0


async def ensure_featured_subscriptions_seeded() -> tuple[int, int]:
    """Copy local subscriptions (called 'featured' in SubWorld) from the Yemen bot DB.

    Runs only when the SubWorld products table is empty. Copies name,
    description, USD price, category, quantity, delivery settings and
    all codes (content, used state, used_by, used_at). The Yemeni riyal
    price is ignored (USD only). Returns (products, codes) seeded.
    """
    from .models import Category, Product, ProductCode

    try:
        async with _new_session() as session:
            existing = (await session.execute(select(Product.id).limit(1))).first()
            if existing is not None:
                return (0, 0)

        from bot.database.main import Database as YemenDatabase
        from bot.database.models.main import (
            LocalSubscription as YemenLocalSubscription,
            LocalSubscriptionCode as YemenCode,
        )

        async with YemenDatabase().session() as ysession:
            subs = (await ysession.execute(
                select(YemenLocalSubscription).order_by(YemenLocalSubscription.id)
            )).scalars().all()
            codes = (await ysession.execute(select(YemenCode))).scalars().all()
            subs_data = [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "price": s.price,
                    "category": s.category or "عام",
                    "quantity": s.quantity or 1,
                    "is_active": s.is_active,
                    "delivery_type": s.delivery_type or "codes",
                    "codes_per_purchase": s.codes_per_purchase or 1,
                    "invite_field_type": s.invite_field_type or "email",
                }
                for s in subs
            ]
            codes_data = [
                {
                    "id": c.id,
                    "subscription_id": c.subscription_id,
                    "content": c.content,
                    "is_used": c.is_used,
                    "used_by": c.used_by,
                    "used_at": c.used_at,
                }
                for c in codes
            ]

        if not subs_data:
            logger.info("SubWorld featured seeding skipped: no local subscriptions in Yemen DB")
            return (0, 0)

        async with _new_session() as session:
            categories: dict[str, int] = {}
            for data in subs_data:
                cat_name = data["category"]
                if cat_name not in categories:
                    cat = Category(name_ar=cat_name, name_en=cat_name)
                    session.add(cat)
                    await session.flush()
                    categories[cat_name] = cat.id
                session.add(Product(
                    id=data["id"],
                    category_id=categories[cat_name],
                    name_ar=data["name"],
                    name_en=data["name"],
                    description_ar=data["description"] or "",
                    description_en=data["description"] or "",
                    price_usd=data["price"],
                    price_sar=None,
                    duration_days=None,
                    delivery_type=data["delivery_type"],
                    codes_per_purchase=data["codes_per_purchase"],
                    invite_field_type=data["invite_field_type"],
                    quantity=data["quantity"],
                    sort_order=data["id"],
                    is_active=data["is_active"],
                ))
            seen_codes: set[str] = set()
            codes_n = 0
            for c in codes_data:
                content = (c["content"] or "").strip()
                if not content or content in seen_codes:
                    continue
                seen_codes.add(content)
                session.add(ProductCode(
                    id=c["id"],
                    product_id=c["subscription_id"],
                    code=content,
                    is_used=bool(c["is_used"]),
                    used_by_user_id=c["used_by"],
                    used_at=c["used_at"],
                ))
                codes_n += 1
            await session.commit()
        logger.info(
            "SubWorld featured subscriptions seeded: %s products, %s codes",
            len(subs_data), codes_n,
        )
        return (len(subs_data), codes_n)
    except Exception as e:
        logger.warning("SubWorld featured seeding failed: %s", e)
        return (0, 0)
