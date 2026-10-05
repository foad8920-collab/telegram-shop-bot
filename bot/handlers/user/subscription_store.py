"""StackVault subscription store presentation handlers."""
from __future__ import annotations

from html import escape
import time
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from bot.i18n import localize
from bot.keyboards.inline import subscription_product_keyboard
from bot.misc.services import StackVaultAPIError, StackVaultClient
from bot.misc.services.stackvault_pricing import calculate_selling_price, is_available as _has_stock

router = Router()
_PRODUCTS_KEY = "stackvault_products"
_MAX_INLINE_PRODUCTS = 80
_CATALOG_CACHE_TTL = 30.0
_catalog_cache: tuple[float, list[dict]] | None = None
# Kept as a compatibility symbol for older integrations; it is not used for
# slicing because the catalog no longer has pages.
PRODUCTS_PER_PAGE = 10


class SubscriptionSearch(StatesGroup):
    waiting_query = State()


def is_available(product: dict) -> bool:
    return product.get("in_stock") is True and _has_stock(product)


def subscription_store_keyboard(products: list[dict], *, show_search: bool = False) -> InlineKeyboardMarkup:
    """One product per row; no pagination and no stock filtering."""
    rows: list[list[InlineKeyboardButton]] = []
    for index, product in enumerate(products):
        status = localize("subscription_store.available" if is_available(product) else "subscription_store.out_of_stock")
        name = str(product.get("name") or localize("subscription_store.product_unnamed"))
        selling_price = product.get("selling_price")
        price = f"${selling_price}" if selling_price is not None else localize("subscription_store.price_unset")
        rows.append([InlineKeyboardButton(
            text=f"{status} | {name[:40]} — {price}",
            callback_data=f"sv_item:{index}",
            style="success" if is_available(product) else "danger",
        )])
    if show_search:
        rows.append([InlineKeyboardButton(text=localize("subscription_store.search"), callback_data="sv_search")])
    rows.append([InlineKeyboardButton(text=localize("btn.back"), callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _load_catalog() -> list[dict] | None:
    global _catalog_cache
    now = time.monotonic()
    if _catalog_cache is not None and now - _catalog_cache[0] < _CATALOG_CACHE_TTL:
        return _catalog_cache[1]
    try:
        async with StackVaultClient() as client:
            source_products = await client.get_products()
    except StackVaultAPIError:
        return None

    products = []
    for product in source_products:
        item = dict(product)
        supplier_price = item.get("price", item.get("supplier_price"))
        selling_price = calculate_selling_price(
            supplier_price,
            item.get("stackvault_pricing_value"),
            item.get("stackvault_pricing_mode"),
        )
        if selling_price is None:
            selling_price = item.get("selling_price", supplier_price)
        item["id"] = str(item.get("id")) if item.get("id") is not None else ""
        item["supplier_price"] = supplier_price
        item["selling_price"] = str(selling_price) if selling_price is not None else None
        item["stock"] = item.get("stock", 0)
        item["in_stock"] = item.get("in_stock") is True
        products.append(item)
    products.sort(key=lambda product: str(product.get("name") or "").casefold())
    _catalog_cache = (now, products)
    return products


async def _render_catalog(event: CallbackQuery | Message, state: FSMContext, query: str = "") -> None:
    products = await _load_catalog()
    if products is None:
        text, visible = localize("subscription_store.unavailable"), []
    else:
        if query:
            needle = query.casefold()
            products = [p for p in products if needle in str(p.get("name") or "").casefold()]
        await state.update_data(**{_PRODUCTS_KEY: products})
        if not products:
            text = localize("subscription_store.no_results") if query else localize("subscription_store.empty")
            visible = []
        elif len(products) > _MAX_INLINE_PRODUCTS and not query:
            text = localize("subscription_store.too_many", count=len(products))
            visible = []
        else:
            text, visible = localize("subscription_store.title"), products
    markup = subscription_store_keyboard(visible, show_search=True)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


async def _show_catalog(call: CallbackQuery, state: FSMContext, page: int = 0) -> None:
    """Compatibility wrapper; ``page`` is intentionally ignored."""
    await _render_catalog(call, state)


@router.callback_query(F.data == "sv_store")
async def subscription_store_handler(call: CallbackQuery, state: FSMContext) -> None:
    await _render_catalog(call, state)


@router.callback_query(F.data == "sv_search")
async def subscription_search_handler(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SubscriptionSearch.waiting_query)
    await call.answer(localize("subscription_store.search_prompt"), show_alert=True)


@router.message(SubscriptionSearch.waiting_query)
async def subscription_search_message_handler(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not query:
        await message.answer(localize("subscription_store.search_prompt"))
        return
    await state.clear()
    await _render_catalog(message, state, query)


@router.callback_query(F.data.startswith("sv_item:"))
async def subscription_product_handler(call: CallbackQuery, state: FSMContext) -> None:
    try:
        index = int((call.data or "").split(":", 1)[1])
    except (IndexError, ValueError):
        await call.answer(localize("subscription_store.product_missing"), show_alert=True)
        return
    products = (await state.get_data()).get(_PRODUCTS_KEY, [])
    if not isinstance(products, list) or not 0 <= index < len(products):
        await call.answer(localize("subscription_store.product_missing"), show_alert=True)
        return
    product = products[index]
    name = escape(str(product.get("name") or localize("subscription_store.product_unnamed")))
    stock = escape(str(product.get("stock") if product.get("stock") is not None else "0"))
    selling_price = product.get("selling_price")
    price = f"${escape(str(selling_price))} — الكمية: {stock}" if selling_price is not None else f"{localize('subscription_store.price_unset')} — الكمية: {stock}"
    status = localize("subscription_store.available" if is_available(product) else "subscription_store.out_of_stock")
    await call.message.edit_text(localize("subscription_store.product.title", name=name, price=price, stock=stock, status=status), reply_markup=subscription_product_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "sv_buy")
async def subscription_purchase_placeholder_handler(call: CallbackQuery) -> None:
    await call.answer(localize("subscription_store.purchase_coming_soon"), show_alert=True)
