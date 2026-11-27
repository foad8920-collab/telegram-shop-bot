"""SubWorld store catalog: browse local products and supplier products.

Prices are USD only. Buying pays from the wallet balance:
- local "codes" products deliver a code immediately;
- supplier / invite / manual products create a pending order for the admin.
Wallet top-up (Binance/crypto proof + admin approval) is the next step;
until then a purchase with insufficient balance explains how to top up.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import F, Router, types
from sqlalchemy import select

from .db import get_session_factory
from .keyboards import CHOOSE_TEXT, TEXTS, choose_kb, get_lang, main_menu_kb
from .models import Order, Product, ProductCode, SubworldUser
from .supplier_models import Supplier, SupplierProduct

logger = logging.getLogger("subworld.catalog")
catalog_router = Router()


def _db_on() -> bool:
    return bool(os.environ.get("SUBWORLD_DATABASE_URL", "").strip())


def _session():
    return get_session_factory()()


def _t(lang: str, key: str) -> str:
    strings = {
        "store_unavailable": {
            "ar": "المتجر غير متاح حاليا (قاعدة البيانات غير مضبوطة).",
            "en": "The store is unavailable right now (database is not configured).",
        },
        "store_title": {
            "ar": "🛍️ المتجر\nاختر القسم:",
            "en": "🛍️ Store\nChoose a section:",
        },
        "local_section": {"ar": "⭐ الاشتراكات المميزة", "en": "⭐ Featured subscriptions"},
        "local_title": {"ar": "⭐ الاشتراكات المميزة:", "en": "⭐ Featured subscriptions:"},
        "empty": {"ar": "لا منتجات حاليا.", "en": "No products right now."},
        "back": {"ar": "🔙 رجوع", "en": "🔙 Back"},
        "main_menu": {"ar": "🔙 القائمة الرئيسية", "en": "🔙 Main menu"},
        "price": {"ar": "💲 السعر", "en": "💲 Price"},
        "stock": {"ar": "📦 المخزون", "en": "📦 Stock"},
        "unlimited": {"ar": "غير محدود", "en": "Unlimited"},
        "duration": {"ar": "⏳ المدة", "en": "⏳ Duration"},
        "days": {"ar": "يوم", "en": "days"},
        "codes_left": {"ar": "كود متاح", "en": "codes available"},
        "buy": {"ar": "🛒 شراء", "en": "🛒 Buy"},
        "balance_title": {"ar": "💰 رصيدك", "en": "💰 Your balance"},
        "balance_line": {"ar": "رصيدك الحالي", "en": "Your current balance"},
        "topup_hint": {
            "ar": "اشحن محفظتك عبر Binance أو الكريبتو، ثم أرسل إثبات الدفع ليضيف المشرف الرصيد.",
            "en": "Top up your wallet via Binance or crypto, then send the payment proof so the admin adds the balance.",
        },
        "not_enough": {"ar": "رصيدك غير كافٍ لهذا المنتج.", "en": "Your balance is not enough for this product."},
        "bought": {"ar": "✅ تم الشراء بنجاح!", "en": "✅ Purchase successful!"},
        "your_code": {"ar": "الكود الخاص بك:", "en": "Your code:"},
        "remaining": {"ar": "رصيدك المتبقي", "en": "Your remaining balance"},
        "order_created": {
            "ar": "✅ تم إنشاء طلبك!\nسيصلك المنتج بعد تأكيد المشرف.",
            "en": "✅ Your order was created!\nYou will receive the product after admin confirmation.",
        },
        "no_codes": {"ar": "⛔ نفدت أكواد هذا المنتج حاليا.", "en": "⛔ This product's codes are sold out right now."},
        "gone": {"ar": "المنتج غير متاح حاليا.", "en": "This product is unavailable right now."},
    }
    return strings[key][lang]


def _fmt_price(value) -> str:
    return f"${Decimal(str(value)):.2f}"


async def _get_user(session, tg_id: int, lang: str) -> SubworldUser:
    user = await session.get(SubworldUser, tg_id)
    if user is None:
        user = SubworldUser(id=tg_id, language=lang, balance_usd=Decimal("0"))
        session.add(user)
        await session.flush()
    return user


def _back_kb(lang: str, target: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=_t(lang, "back"), callback_data=target)],
            [types.InlineKeyboardButton(text=_t(lang, "main_menu"), callback_data="menu:main")],
        ]
    )


# ============================================================
#  Main menu / balance / language
# ============================================================

@catalog_router.callback_query(F.data == "menu:main")
async def on_menu_main(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    await callback.message.edit_text(TEXTS[lang]["welcome"], reply_markup=main_menu_kb(lang))
    await callback.answer()


@catalog_router.callback_query(F.data == "menu:lang")
async def on_menu_lang(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text(CHOOSE_TEXT, reply_markup=choose_kb())
    await callback.answer()


@catalog_router.callback_query(F.data == "menu:balance")
async def on_menu_balance(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    try:
        async with _session() as session:
            user = await _get_user(session, callback.from_user.id, lang)
            balance = Decimal(str(user.balance_usd or 0))
    except Exception:
        logger.exception("SubWorld balance read failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    text = f"{_t(lang, 'balance_title')}\n{_t(lang, 'balance_line')}: {_fmt_price(balance)}\n\n{_t(lang, 'topup_hint')}"
    await callback.message.edit_text(text, reply_markup=_back_kb(lang, "menu:main"))
    await callback.answer()


# ============================================================
#  Store sections
# ============================================================

@catalog_router.callback_query(F.data == "menu:shop")
async def on_menu_shop(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    try:
        async with _session() as session:
            rows = (await session.execute(
                select(Supplier).where(Supplier.is_active == True).order_by(Supplier.sort_order, Supplier.id)  # noqa: E712
            )).scalars().all()
            suppliers = [r for r in rows if not (r.api_url or "").strip().startswith("local://")]
    except Exception:
        logger.exception("SubWorld store sections failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:main"))
        await callback.answer()
        return
    buttons = [[types.InlineKeyboardButton(text=_t(lang, "local_section"), callback_data="shop:local_list")]]
    for sup in suppliers:
        label = (sup.display_name or sup.name or f"Supplier {sup.id}")[:40]
        buttons.append([types.InlineKeyboardButton(text=f"🏬 {label}", callback_data=f"shop:sup:{sup.id}")])
    buttons.append([types.InlineKeyboardButton(text=_t(lang, "main_menu"), callback_data="menu:main")])
    await callback.message.edit_text(
        _t(lang, "store_title"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@catalog_router.callback_query(F.data == "shop:local_list")
async def on_local_list(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        async with _session() as session:
            rows = (await session.execute(
                select(Product).where(Product.is_active == True).order_by(Product.sort_order, Product.id)  # noqa: E712
            )).scalars().all()
    except Exception:
        logger.exception("SubWorld local list failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    if not rows:
        await callback.message.edit_text(_t(lang, "empty"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    buttons = []
    for p in rows:
        name = (p.name_en if lang == "en" else p.name_ar)[:28]
        buttons.append([types.InlineKeyboardButton(
            text=f"{name} — {_fmt_price(p.price_usd)}",
            callback_data=f"shop:local:{p.id}",
        )])
    buttons.append([types.InlineKeyboardButton(text=_t(lang, "back"), callback_data="menu:shop")])
    await callback.message.edit_text(
        _t(lang, "local_title"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@catalog_router.callback_query(F.data.startswith("shop:sup:"))
async def on_supplier_list(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        supplier_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    try:
        async with _session() as session:
            supplier = await session.get(Supplier, supplier_id)
            rows = (await session.execute(
                select(SupplierProduct).where(
                    SupplierProduct.supplier_id == supplier_id,
                    SupplierProduct.is_visible == True,  # noqa: E712
                    SupplierProduct.is_available == True,  # noqa: E712
                ).order_by(SupplierProduct.selling_price, SupplierProduct.id)
            )).scalars().all()
    except Exception:
        logger.exception("SubWorld supplier list failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    title = (supplier.display_name or supplier.name) if supplier else f"Supplier {supplier_id}"
    if not rows:
        await callback.message.edit_text(f"🏬 {title}\n{_t(lang, 'empty')}", reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    buttons = []
    for p in rows:
        buttons.append([types.InlineKeyboardButton(
            text=f"{p.name[:28]} — {_fmt_price(p.selling_price or 0)}",
            callback_data=f"shop:sprod:{p.id}",
        )])
    buttons.append([types.InlineKeyboardButton(text=_t(lang, "back"), callback_data="menu:shop")])
    await callback.message.edit_text(
        f"🏬 {title}:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# ============================================================
#  Product details
# ============================================================

@catalog_router.callback_query(F.data.startswith("shop:local:"))
async def on_local_detail(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        product_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    try:
        async with _session() as session:
            product = await session.get(Product, product_id)
            codes_left = None
            if product is not None and (product.delivery_type or "codes") == "codes":
                codes_left = (await session.execute(
                    select(ProductCode.id).where(
                        ProductCode.product_id == product_id,
                        ProductCode.is_used == False,  # noqa: E712
                    )
                )).scalars().all()
                codes_left = len(codes_left)
    except Exception:
        logger.exception("SubWorld local detail failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "shop:local_list"))
        await callback.answer()
        return
    if product is None or not product.is_active:
        await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, "shop:local_list"))
        await callback.answer()
        return
    name = product.name_en if lang == "en" else product.name_ar
    desc = (product.description_en if lang == "en" else product.description_ar) or ""
    lines = [f"<b>{name}</b>"]
    if desc:
        lines.append(desc[:800])
    lines.append(f"{_t(lang, 'price')}: {_fmt_price(product.price_usd)}")
    if product.duration_days:
        lines.append(f"{_t(lang, 'duration')}: {product.duration_days} {_t(lang, 'days')}")
    if codes_left is not None:
        lines.append(f"{_t(lang, 'stock')}: {codes_left} {_t(lang, 'codes_left')}")
    elif (product.delivery_type or "codes") != "codes" and product.quantity is not None:
        lines.append(f"{_t(lang, 'stock')}: {product.quantity}")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=_t(lang, "buy"), callback_data=f"shop:buy:local:{product.id}")],
        [types.InlineKeyboardButton(text=_t(lang, "back"), callback_data="shop:local_list")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@catalog_router.callback_query(F.data.startswith("shop:sprod:"))
async def on_supplier_detail(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    try:
        product_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer()
        return
    try:
        async with _session() as session:
            product = await session.get(SupplierProduct, product_id)
            supplier_name = None
            if product is not None:
                supplier = await session.get(Supplier, product.supplier_id)
                supplier_name = (supplier.display_name or supplier.name) if supplier else None
    except Exception:
        logger.exception("SubWorld supplier detail failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    if product is None or not product.is_visible or not product.is_available:
        await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, "menu:shop"))
        await callback.answer()
        return
    lines = [f"<b>{product.name}</b>"]
    if product.description:
        lines.append(product.description[:800])
    lines.append(f"{_t(lang, 'price')}: {_fmt_price(product.selling_price or 0)}")
    stock = _t(lang, "unlimited") if product.is_unlimited else str(product.stock or 0)
    lines.append(f"{_t(lang, 'stock')}: {stock}")
    if supplier_name:
        lines.append(f"🏬 {supplier_name}")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=_t(lang, "buy"), callback_data=f"shop:buy:sup:{product.id}")],
        [types.InlineKeyboardButton(text=_t(lang, "back"), callback_data=f"shop:sup:{product.supplier_id}")],
    ])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


# ============================================================
#  Buying from the wallet balance
# ============================================================

def _insufficient_text(lang: str, balance: Decimal, price: Decimal) -> str:
    return (
        f"{_t(lang, 'not_enough')}\n"
        f"{_t(lang, 'balance_line')}: {_fmt_price(balance)}\n"
        f"{_t(lang, 'price')}: {_fmt_price(price)}\n\n"
        f"{_t(lang, 'topup_hint')}"
    )


@catalog_router.callback_query(F.data.startswith("shop:buy:"))
async def on_buy(callback: types.CallbackQuery) -> None:
    lang = get_lang(callback.from_user.id)
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    _, _, kind, raw_id = parts
    try:
        product_id = int(raw_id)
    except ValueError:
        await callback.answer()
        return
    back_target = "shop:local_list" if kind == "local" else "menu:shop"
    if not _db_on():
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, back_target))
        await callback.answer()
        return
    try:
        async with _session() as session:
            user = await _get_user(session, callback.from_user.id, lang)
            balance = Decimal(str(user.balance_usd or 0))
            if kind == "local":
                product = await session.get(Product, product_id)
                if product is None or not product.is_active:
                    await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target))
                    await callback.answer()
                    return
                price = Decimal(str(product.price_usd))
                name = product.name_en if lang == "en" else product.name_ar
                if balance < price:
                    await callback.message.edit_text(
                        _insufficient_text(lang, balance, price),
                        reply_markup=_back_kb(lang, back_target),
                    )
                    await callback.answer()
                    return
                code_row = None
                if (product.delivery_type or "codes") == "codes":
                    code_row = (await session.execute(
                        select(ProductCode).where(
                            ProductCode.product_id == product_id,
                            ProductCode.is_used == False,  # noqa: E712
                        ).order_by(ProductCode.id).limit(1)
                    )).scalars().first()
                    if code_row is None:
                        await callback.message.edit_text(_t(lang, "no_codes"), reply_markup=_back_kb(lang, back_target))
                        await callback.answer()
                        return
                user.balance_usd = balance - price
                order = Order(
                    user_id=callback.from_user.id,
                    product_id=product_id,
                    amount_usd=price,
                    status="delivered" if code_row is not None else "pending",
                )
                session.add(order)
                await session.flush()
                delivered_code = None
                if code_row is not None:
                    code_row.is_used = True
                    code_row.used_by_user_id = callback.from_user.id
                    code_row.used_at = datetime.now(timezone.utc)
                    order.code_id = code_row.id
                    delivered_code = code_row.code
                await session.commit()
                new_balance = balance - price
                if delivered_code is not None:
                    text = (
                        f"{_t(lang, 'bought')}\n"
                        f"{name}\n"
                        f"{_t(lang, 'price')}: {_fmt_price(price)}\n"
                        f"{_t(lang, 'remaining')}: {_fmt_price(new_balance)}\n\n"
                        f"{_t(lang, 'your_code')}:\n"
                        f"<code>{delivered_code}</code>"
                    )
                else:
                    text = (
                        f"{_t(lang, 'order_created')}\n"
                        f"{name}\n"
                        f"{_t(lang, 'price')}: {_fmt_price(price)}\n"
                        f"{_t(lang, 'remaining')}: {_fmt_price(new_balance)}"
                    )
                await callback.message.edit_text(text, reply_markup=_back_kb(lang, "menu:shop"))
                await callback.answer()
                return
            # Supplier product: pending order, admin delivers.
            product = await session.get(SupplierProduct, product_id)
            if product is None or not product.is_visible or not product.is_available:
                await callback.message.edit_text(_t(lang, "gone"), reply_markup=_back_kb(lang, back_target))
                await callback.answer()
                return
            price = Decimal(str(product.selling_price or 0))
            if balance < price:
                await callback.message.edit_text(
                    _insufficient_text(lang, balance, price),
                    reply_markup=_back_kb(lang, back_target),
                )
                await callback.answer()
                return
            user.balance_usd = balance - price
            order = Order(
                user_id=callback.from_user.id,
                supplier_product_id=product_id,
                amount_usd=price,
                status="pending",
            )
            session.add(order)
            await session.commit()
            logger.info(
                "SubWorld supplier order %s: user %s bought %s for $%s (pending delivery)",
                order.id, callback.from_user.id, product.name, price,
            )
            text = (
                f"{_t(lang, 'order_created')}\n"
                f"{product.name}\n"
                f"{_t(lang, 'price')}: {_fmt_price(price)}\n"
                f"{_t(lang, 'remaining')}: {_fmt_price(balance - price)}"
            )
            await callback.message.edit_text(text, reply_markup=_back_kb(lang, "menu:shop"))
            await callback.answer()
            return
    except Exception:
        logger.exception("SubWorld buy failed")
        await callback.message.edit_text(_t(lang, "store_unavailable"), reply_markup=_back_kb(lang, back_target))
        await callback.answer()
        return
