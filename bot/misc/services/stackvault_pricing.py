"""Local selling prices; supplier costs must never become retail defaults."""

from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from bot.database import Database
from bot.database.models import StackVaultPriceOverride, StackVaultPricingSettings


# Keys are stable StackVault product IDs, not names or page positions.
# Leave unpriced products unset until a retail price is explicitly configured.
DEFAULT_MARKUP_PERCENTAGE = Decimal("30.00")


def _money(value) -> str | None:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        return format(amount.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, ValueError, TypeError):
        return None


def calculate_selling_price(supplier_price, pricing_value=None, pricing_mode=None) -> str | None:
    """Calculate a retail price using a validated markup percentage."""
    try:
        supplier = Decimal(str(supplier_price))
        value = Decimal(str(pricing_value)) if pricing_value is not None else DEFAULT_MARKUP_PERCENTAGE
        if not supplier.is_finite() or not value.is_finite() or supplier < 0 or value < 0:
            return None
        amount = supplier + (supplier * value / Decimal("100"))
        return _money(amount)
    except (InvalidOperation, ValueError, TypeError):
        return None


async def get_selling_prices(
    product_ids: list[str], supplier_prices: dict[str, object] | None = None
) -> dict[str, str | None]:
    """Resolve StackVault selling prices from settings and product-id overrides."""
    supplier_prices = supplier_prices or {}
    async with Database().session() as session:
        settings = (await session.execute(select(StackVaultPricingSettings))).scalars().first()
        markup = DEFAULT_MARKUP_PERCENTAGE
        if settings is not None:
            try:
                candidate = Decimal(str(settings.default_markup_percentage))
                if candidate.is_finite() and candidate >= 0:
                    markup = candidate
            except (InvalidOperation, ValueError, TypeError):
                pass
        overrides = (await session.execute(
            select(StackVaultPriceOverride).where(StackVaultPriceOverride.product_id.in_(product_ids))
        )).scalars().all() if product_ids else []
    override_map = {str(row.product_id): row.manual_price for row in overrides}
    result = {}
    for product_id in product_ids:
        if product_id in override_map:
            result[product_id] = _money(override_map[product_id])
        else:
            result[product_id] = calculate_selling_price(supplier_prices.get(product_id), markup)
    return result


async def prepare_catalog(products: list[dict]) -> list[dict]:
    """Keep every product and separate supplier prices from local retail prices.

    Returned values are JSON-safe for Redis FSM storage. An absent product ID
    or local price leaves selling_price unset; API selling prices are ignored.
    """
    ids = [str(p["id"]) for p in products if p.get("id") is not None]
    supplier_prices = {str(p["id"]): p.get("price") for p in products if p.get("id") is not None}
    prices = await get_selling_prices(ids, supplier_prices) if ids else {}
    result = []
    for product in products:
        item = dict(product)
        item["supplier_price"] = _money(item.pop("price", None))
        key = str(item["id"]) if item.get("id") is not None else None
        item["selling_price"] = prices.get(key)
        result.append(item)
    return result


def is_available(product: dict) -> bool:
    try:
        stock = Decimal(str(product.get("stock", 0)))
        return stock.is_finite() and stock > 0
    except (InvalidOperation, ValueError, TypeError):
        return False
