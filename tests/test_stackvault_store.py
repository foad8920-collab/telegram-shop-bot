from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.handlers.user.subscription_store import PRODUCTS_PER_PAGE, _show_catalog, subscription_product_handler, subscription_store_keyboard
from bot.keyboards.inline import main_menu
from bot.misc.services.stackvault import StackVaultAPIError, StackVaultClient


class _Response:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    async def json(self, **kwargs):
        return self.payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.get = MagicMock(return_value=_Response(payload))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_stackvault_client_returns_products(monkeypatch):
    monkeypatch.setattr("bot.misc.services.stackvault.EnvKeys.STACKVAULT_API_KEY", "test-key")
    session = _Session({"ok": True, "products": [{"name": "Pro", "price": 5, "stock": 2}]})
    with patch("bot.misc.services.stackvault.aiohttp.ClientSession", return_value=session):
        products = await StackVaultClient().get_products()
    assert products == [{"name": "Pro", "price": 5, "stock": 2}]
    assert session.get.call_args.args[0].endswith("/products")


@pytest.mark.asyncio
async def test_stackvault_client_rejects_failed_api_response(monkeypatch):
    monkeypatch.setattr("bot.misc.services.stackvault.EnvKeys.STACKVAULT_API_KEY", "test-key")
    with patch("bot.misc.services.stackvault.aiohttp.ClientSession", return_value=_Session({"ok": False})):
        with pytest.raises(StackVaultAPIError):
            await StackVaultClient().get_products()


def test_main_menu_includes_subscription_store():
    callbacks = [button.callback_data for row in main_menu(role=1).inline_keyboard for button in row]
    assert "sv_store" in callbacks


def test_subscription_store_keyboard_has_product_and_pagination():
    markup = subscription_store_keyboard([{"name": "Pro", "price": 5}], page=1, total_pages=3)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "sv_item:0" in callbacks
    assert "sv_page:0" in callbacks
    assert "sv_page:2" in callbacks


@pytest.mark.parametrize("in_stock,stock,expected", [
    (True, 3, "success"),
    (True, 0, "danger"),
    (False, 3, "success"),
    (False, 0, "danger"),
    (True, -1, "danger"),
    (None, None, "danger"),
])
def test_product_button_stock_style_is_serialized(in_stock, stock, expected):
    markup = subscription_store_keyboard(
        [{"name": "Pro", "price": 5, "in_stock": in_stock, "stock": stock}],
        page=1, total_pages=3,
    )
    buttons = [button for row in markup.model_dump(mode="json", exclude_none=True)["inline_keyboard"] for button in row]
    assert buttons[0]["style"] == expected
    assert buttons[0]["callback_data"] == "sv_item:0"
    assert all("style" not in button for button in buttons[1:])


@pytest.mark.asyncio
async def test_catalog_shows_ten_products_per_page(make_callback_query, fsm_context):
    products = [{"name": f"P{i}", "price": i, "stock": 1} for i in range(PRODUCTS_PER_PAGE + 1)]
    call = make_callback_query(data="sv_store")
    with patch("bot.handlers.user.subscription_store.StackVaultClient.get_products", new=AsyncMock(return_value=products)):
        await _show_catalog(call, fsm_context)
    markup = call.message.edit_text.call_args.kwargs["reply_markup"]
    product_callbacks = [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data and button.callback_data.startswith("sv_item:")]
    assert len(product_callbacks) == PRODUCTS_PER_PAGE


@pytest.mark.asyncio
async def test_product_card_uses_current_page_product(make_callback_query, fsm_context):
    await fsm_context.update_data(stackvault_page_products=[{"name": "Pro <Plan>", "price": "9.99", "stock": 7}])
    call = make_callback_query(data="sv_item:0")
    await subscription_product_handler(call, fsm_context)
    text = call.message.edit_text.call_args.args[0]
    assert "Pro &lt;Plan&gt;" in text
    assert "9.99" not in text


@pytest.mark.asyncio
async def test_local_prices_are_separate_and_unpriced_products_remain_visible(monkeypatch, make_callback_query, fsm_context):
    from bot.misc.services.stackvault_pricing import prepare_catalog
    monkeypatch.setattr("bot.misc.services.stackvault_pricing.SELLING_PRICES_USD", {"11": "18.50"})
    products = [
        {"id": 11, "name": "Available", "price": "7.31", "stock": 2},
        {"id": 12, "name": "Sold out", "price": "4.27", "stock": 0, "selling_price": "4.27"},
    ]
    prepared = await prepare_catalog(products)
    assert prepared[0]["supplier_price"] == "7.31"
    assert prepared[0]["selling_price"] == "18.50"
    assert prepared[1]["supplier_price"] == "4.27"
    assert prepared[1]["selling_price"] is None
    assert all("price" not in product for product in prepared)
    assert products[0]["price"] == "7.31"
    call = make_callback_query(data="sv_store")
    with patch("bot.handlers.user.subscription_store.StackVaultClient.get_products", new=AsyncMock(return_value=products)):
        await _show_catalog(call, fsm_context)
    markup = call.message.edit_text.call_args.kwargs["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row if (b.callback_data or "").startswith("sv_item:")]
    assert len(buttons) == 2
    assert "18.50" in buttons[0].text and "🟢" in buttons[0].text
    assert "🔴" in buttons[1].text
    assert "7.31" not in str(markup) and "4.27" not in str(markup)
    call.data = "sv_item:1"
    await subscription_product_handler(call, fsm_context)
    text = call.message.edit_text.call_args.args[0]
    assert "🔴" in text and "4.27" not in text


@pytest.mark.asyncio
async def test_zero_stock_api_products_are_not_filtered(monkeypatch):
    monkeypatch.setattr("bot.misc.services.stackvault.EnvKeys.STACKVAULT_API_KEY", "test-key")
    products = [{"id": 1, "stock": 0}, {"id": 2, "stock": 8}]
    with patch("bot.misc.services.stackvault.aiohttp.ClientSession", return_value=_Session({"ok": True, "products": products})):
        assert await StackVaultClient().get_products() == products
