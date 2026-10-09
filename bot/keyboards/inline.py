from typing import Callable, Iterable, Tuple
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.i18n import localize
from bot.database.models import Permission
from bot.misc import LazyPaginator # noqa: F401
from bot.misc.services.stackvault_pricing import is_available


def main_menu(role: int, channel: str | None = None, helper: str | None = None) -> InlineKeyboardMarkup:
    """
    Main menu with a balanced two-button layout for every screen size.
    """
    kb = InlineKeyboardBuilder()
    # Pair related actions to keep the menu compact without overcrowding buttons.
    kb.row(
        InlineKeyboardButton(text=localize("btn.shop"), callback_data="shop", style="success"),
        InlineKeyboardButton(text=localize("btn.search"), callback_data="shop_search", style="primary"),
    )
    kb.row(
        InlineKeyboardButton(text=localize("btn.profile"), callback_data="profile", style="primary"),
        InlineKeyboardButton(text=localize("btn.rules"), callback_data="rules", style="primary"),
    )
    kb.row(InlineKeyboardButton(text=localize("btn.subscription_store"), callback_data="sv_store", style="primary"))

    row3 = []
    if helper:
        row3.append(InlineKeyboardButton(text=localize("btn.support"), url=f"tg://user?id={helper}", style="primary"))
    row3.append(InlineKeyboardButton(text=localize("btn.language"), callback_data="choose_language", style="primary"))
    kb.row(*row3)

    extra_row = []
    if channel:
        extra_row.append(InlineKeyboardButton(text=localize("btn.channel"), url=f"https://t.me/{channel.lstrip('@')}", style="primary"))
    if Permission.has_any_admin_perm(role):
        extra_row.append(InlineKeyboardButton(text=localize("btn.admin_menu"), callback_data="console", style="primary"))
    if extra_row:
        kb.row(*extra_row)

    return kb.as_markup()


def shop_source_keyboard() -> InlineKeyboardMarkup:
    """Choose the subscription source before entering a store flow."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🟢 اشتراكات StackVault", callback_data="shop_stackvault"))
    kb.row(InlineKeyboardButton(text="🟣 اشتراكات Pandora", callback_data="shop_pandora"))
    kb.row(InlineKeyboardButton(text="🔵 الاشتراكات المحلية", callback_data="shop_local"))
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="back_to_menu"))
    return kb.as_markup()

def subscription_store_keyboard(products: list[dict], page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build the StackVault subscription-store product list and navigation."""
    kb = InlineKeyboardBuilder()
    for index, product in enumerate(products):
        name = str(product.get("name") or localize("subscription_store.product_unnamed"))
        selling_price = product.get("selling_price")
        price = f"${selling_price}" if selling_price is not None else localize("subscription_store.price_unset")
        status = localize("subscription_store.available" if is_available(product) else "subscription_store.out_of_stock")
        label = f"{status} | {name[:40]} — {price}"
        kb.row(InlineKeyboardButton(text=label, callback_data=f"sv_item:{index}"))

    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"sv_page:{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="dummy_button"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"sv_page:{page + 1}"))
        kb.row(*nav_buttons)

    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="back_to_menu"))
    return kb.as_markup()


def subscription_product_keyboard() -> InlineKeyboardMarkup:
    """Product card actions. Purchasing is deliberately a placeholder for now."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=localize("btn.buy"), callback_data="sv_buy"))
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="sv_store"))
    return kb.as_markup()


def pandora_products_keyboard(products: list[dict], page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build a read-only Pandora catalog page."""
    kb = InlineKeyboardBuilder()
    for index, product in enumerate(products):
        name = str(product.get("name") or product.get("title") or "Pandora subscription")
        price = product.get("supplier_price", product.get("price"))
        currency = str(product.get("currency") or product.get("currency_code") or "غير محددة")
        price_text = f"{price} {currency}" if price is not None else "?"
        kb.row(InlineKeyboardButton(
            text=f"{name[:38]} — {price_text}",
            callback_data=f"pditm:{index}:{page}",
        ))
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pdpg_{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="dummy_button"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pdpg_{page + 1}"))
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="shop"))
    return kb.as_markup()


def pandora_product_keyboard(page: int) -> InlineKeyboardMarkup:
    """Read-only Pandora product actions."""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🛒 الشراء — قريبًا", callback_data="pd_buy_soon"))
    kb.row(InlineKeyboardButton(text="◀️ العودة للمنتجات", callback_data=f"pdpg_{page}"))
    return kb.as_markup()

def language_menu() -> InlineKeyboardMarkup:
    """
    Language selection menu.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🇸🇦 العربية", callback_data="set_lang:ar"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en"),
    )
    kb.row(
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru"),
        InlineKeyboardButton(text=localize("btn.back"), callback_data="back_to_menu"),
    )
    return kb.as_markup()


def profile_keyboard(referral_percent: int, user_items: int = 0, cart_count: int = 0) -> InlineKeyboardMarkup:
    """
    Profile keyboard: balanced 2-column layout.
    """
    kb = InlineKeyboardBuilder()
    cart_text = localize("btn.cart", count=cart_count) if cart_count > 0 else localize("btn.cart_empty")
    # Row 1: Balance & Cart
    kb.row(
        InlineKeyboardButton(text=localize("btn.replenish"), callback_data="replenish_balance"),
        InlineKeyboardButton(text=cart_text, callback_data="cart"),
    )
    # Row 2: Referrals & Purchases
    mid_row = []
    if referral_percent != 0:
        mid_row.append(InlineKeyboardButton(text=localize("btn.referral"), callback_data="referral_system"))
    if user_items != 0:
        mid_row.append(InlineKeyboardButton(text=localize("btn.purchased"), callback_data="bought_items"))
    if mid_row:
        kb.row(*mid_row)

    # Row 3: History & Promo
    kb.row(
        InlineKeyboardButton(text=localize("btn.operation_history"), callback_data="operation_history"),
        InlineKeyboardButton(text=localize("btn.redeem_promo"), callback_data="redeem_promo"),
    )
    # Row 4: Back
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="back_to_menu"))
    return kb.as_markup()


def admin_console_keyboard(maintenance_mode: bool = False, role: int = 127) -> InlineKeyboardMarkup:
    """
    Admin panel — shows only buttons the user has permissions for, in a 2-column layout.
    """
    kb = InlineKeyboardBuilder()
    if role & Permission.CATALOG_MANAGE:
        kb.button(text=localize("admin.menu.shop"), callback_data="shop_management")
        kb.button(text=localize("admin.menu.goods"), callback_data="goods_management")
        kb.button(text=localize("admin.menu.categories"), callback_data="categories_management")
    if role & Permission.PROMO_MANAGE:
        kb.button(text=localize("admin.menu.promo"), callback_data="promo_mgmt")
    if role & Permission.USERS_MANAGE:
        kb.button(text=localize("admin.menu.users"), callback_data="user_management")
    if role & Permission.ADMINS_MANAGE:
        kb.button(text=localize("admin.menu.roles"), callback_data="role_mgmt")
    if role & Permission.BROADCAST:
        kb.button(text=localize("admin.menu.broadcast"), callback_data="send_message")
    if role & Permission.SETTINGS_MANAGE:
        maintenance_key = "admin.menu.maintenance_on" if maintenance_mode else "admin.menu.maintenance_off"
        kb.button(text=localize(maintenance_key), callback_data="toggle_maintenance")
    kb.button(text="🔌 StackVault Test", callback_data="stackvault_test")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="back_to_menu"))
    return kb.as_markup()


def simple_buttons(buttons: Iterable[Tuple[str, str]], per_row: int = 2) -> InlineKeyboardMarkup:
    """
    Universal button assembly from (text, callback_data) with clean 2-column layout.
    """
    btn_list = list(buttons)
    if not btn_list:
        return InlineKeyboardMarkup(inline_keyboard=[])

    last_text, last_cb = btn_list[-1]
    is_back = (
        last_cb in ("console", "back_to_menu", "profile", "goods_management", "shop_management", "user_management", "replenish_balance", "shop")
        or "back" in last_cb.lower()
    )

    kb = InlineKeyboardBuilder()
    if is_back and len(btn_list) > 1 and len(btn_list) % 2 == 0:
        for text, cb in btn_list[:-1]:
            kb.button(text=text, callback_data=cb)
        kb.adjust(per_row)
        kb.row(InlineKeyboardButton(text=last_text, callback_data=last_cb))
    else:
        for text, cb in btn_list:
            kb.button(text=text, callback_data=cb)
        kb.adjust(per_row)

    return kb.as_markup()


def back(cb: str = "menu", text: str | None = None) -> InlineKeyboardMarkup:
    """
    One 'Back' button.
    """
    return simple_buttons([(text or localize("btn.back"), cb)])


def close() -> InlineKeyboardMarkup:
    """
    One button 'Close'.
    """
    return simple_buttons([(localize("btn.close"), "close")])


async def lazy_paginated_keyboard(
        paginator: 'LazyPaginator',
        item_text: Callable[[object], str],
        item_callback: Callable[[object], str],
        page: int = 0,
        back_cb: str | None = None,
        nav_cb_prefix: str = "",
        back_text: str | None = None,
        extra_rows: list[list[InlineKeyboardButton]] | None = None,
        item_style: Callable[[object], str | None] | str | None = None,
) -> InlineKeyboardMarkup:
    """
    Lazy pagination keyboard with data loading on demand.

    `extra_rows` are inserted between the item buttons and the navigation row.
    """
    kb = InlineKeyboardBuilder()

    # Get items for current page
    items = await paginator.get_page(page)

    for item in items:
        style = item_style(item) if callable(item_style) else item_style
        kb.button(text=item_text(item), callback_data=item_callback(item), style=style)
    kb.adjust(1)

    for row in (extra_rows or []):
        kb.row(*row)

    # Navigation
    total_pages = await paginator.get_total_pages()
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"{nav_cb_prefix}{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="dummy_button"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"{nav_cb_prefix}{page + 1}"))
        kb.row(*nav_buttons)

    if back_cb:
        kb.row(InlineKeyboardButton(text=back_text or localize("btn.back"), callback_data=back_cb))

    return kb.as_markup()


def item_info(
        back_data: str, avg_rating: float = None,
        review_count: int = 0, has_purchased: bool = False,
        applied_promo: str = None, reviews_enabled: bool = True,
        out_of_stock: bool = False, subscribed: bool = False,
) -> InlineKeyboardMarkup:
    """
    Product card with buy, cart, promo, review buttons.

    When `out_of_stock`, offers a restock notification toggle instead of
    leaving the user at a dead end.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=localize("btn.buy"), callback_data="buy_item")
    kb.button(text=localize("btn.add_to_cart"), callback_data="add_to_cart")
    if applied_promo:
        kb.button(text=localize("btn.remove_promo"), callback_data="remove_promo")
    else:
        kb.button(text=localize("btn.apply_promo"), callback_data="apply_promo")
    if reviews_enabled:
        if review_count > 0:
            kb.button(text=localize("btn.view_reviews", count=review_count), callback_data="reviews:0")
        if has_purchased:
            kb.button(text=localize("btn.leave_review"), callback_data="review")
    if out_of_stock:
        if subscribed:
            kb.button(text=localize("btn.notify_stock_off"), callback_data="unsub_stock")
        else:
            kb.button(text=localize("btn.notify_stock"), callback_data="sub_stock")
    kb.button(text=localize("btn.back"), callback_data=back_data)
    kb.adjust(2)
    return kb.as_markup()


def cart_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    """
    Cart view: quantity stepper per item and 2-column actions at the bottom.
    """
    kb = InlineKeyboardBuilder()
    for item in items:
        kb.row(
            InlineKeyboardButton(text="➖", callback_data=f"cart_qty:{item['id']}:-1"),
            InlineKeyboardButton(
                text=f"{item['item_name']} ×{item['quantity']}",
                callback_data="dummy_button",
            ),
            InlineKeyboardButton(text="➕", callback_data=f"cart_qty:{item['id']}:1"),
        )
        if item.get('promo_code'):
            kb.row(InlineKeyboardButton(
                text=localize("btn.cart_remove_promo", code=item['promo_code']),
                callback_data=f"cart_unpromo:{item['id']}",
            ))
        kb.row(InlineKeyboardButton(
            text=localize("btn.cart_remove_item", name=item['item_name']),
            callback_data=f"cart_remove:{item['id']}",
        ))
    kb.row(
        InlineKeyboardButton(text=localize("btn.cart_checkout"), callback_data="cart_checkout"),
        InlineKeyboardButton(text=localize("btn.cart_clear"), callback_data="cart_clear"),
    )
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="profile"))
    return kb.as_markup()


def payment_menu(pay_url: str) -> InlineKeyboardMarkup:
    """
    Buttons under the invoice (CryptoPay, etc.).
    """
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=localize("btn.pay"), url=pay_url))
    kb.row(
        InlineKeyboardButton(text=localize("btn.check_payment"), callback_data="check"),
        InlineKeyboardButton(text=localize("btn.back"), callback_data="profile"),
    )
    return kb.as_markup()


def get_payment_choice() -> InlineKeyboardMarkup:
    """
    Select a payment method in a 2-column grid.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=localize("btn.pay.crypto"), callback_data="pay_cryptopay"),
        InlineKeyboardButton(text=localize("btn.pay.stars"), callback_data="pay_stars"),
    )
    kb.row(
        InlineKeyboardButton(text=localize("btn.pay.tg"), callback_data="pay_fiat"),
        InlineKeyboardButton(text=localize("btn.back"), callback_data="replenish_balance"),
    )
    return kb.as_markup()


def question_buttons(question: str, back_data: str) -> InlineKeyboardMarkup:
    """
    Universal yes/no + Back.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text=localize("btn.yes"), callback_data=f"{question}_yes")
    kb.button(text=localize("btn.no"), callback_data=f"{question}_no")
    kb.button(text=localize("btn.back"), callback_data=back_data)
    kb.adjust(2)
    return kb.as_markup()


def check_sub(channel_username: str) -> InlineKeyboardMarkup:
    """
    checks the channel subscription.
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=localize("btn.channel"), url=f"https://t.me/{channel_username}"),
        InlineKeyboardButton(text=localize("btn.check_subscription"), callback_data="sub_channel_done"),
    )
    return kb.as_markup()


def rating_keyboard() -> InlineKeyboardMarkup:
    """Rating selection keyboard (1-5 stars)."""
    kb = InlineKeyboardBuilder()
    for i in range(1, 6):
        kb.button(text="⭐" * i, callback_data=f"rating:{i}")
    kb.button(text=localize("btn.back"), callback_data="back_to_menu")
    kb.adjust(5)
    return kb.as_markup()


def referral_system_keyboard(has_referrals: bool = False, has_earnings: bool = False) -> InlineKeyboardMarkup:
    """
    Referral system keyboard with 2-column buttons.
    """
    kb = InlineKeyboardBuilder()
    action_buttons = []
    if has_referrals:
        action_buttons.append(InlineKeyboardButton(text=localize("btn.view_referrals"), callback_data="view_referrals"))
    if has_earnings:
        action_buttons.append(InlineKeyboardButton(text=localize("btn.view_earnings"), callback_data="view_all_earnings"))
    if action_buttons:
        kb.row(*action_buttons)
    kb.row(InlineKeyboardButton(text=localize("btn.back"), callback_data="profile"))
    return kb.as_markup()
