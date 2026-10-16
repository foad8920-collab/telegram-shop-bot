"""Read-only client for the Pandora Digital product catalog."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import aiohttp

from bot.misc import EnvKeys


class PandoraAPIError(RuntimeError):
    """The Pandora catalog could not be retrieved or parsed."""


def calculate_selling_price(supplier_price: Any) -> Decimal | None:
    """Apply the Pandora catalog markup without touching shared pricing."""
    if supplier_price is None:
        return None
    try:
        price = Decimal(str(supplier_price))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return (price * Decimal("1.30")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class PandoraClient:
    """Fetch all products from Pandora using cursor pagination."""

    DEFAULT_BASE_URL = "https://api.pandoradigital.shop/api/v1"
    _timeout = aiohttp.ClientTimeout(total=30)

    def __init__(self, api_url: str | None = None, api_key: str | None = None) -> None:
        self._api_url_override = api_url
        self._api_key_override = api_key
        self._session: aiohttp.ClientSession | None = None
        self.pages_fetched = 0

    async def _load_credentials(self) -> tuple[str, str]:
        """قراءة api_url و api_key من DB (id=2) مع fallback إلى env."""
        if self._api_url_override and self._api_key_override:
            return self._api_url_override, self._api_key_override
        try:
            from bot.database.main import Database
            from bot.database.models.main import Supplier
            async with Database().session() as session:
                sup = await session.get(Supplier, 2)
                if sup and sup.api_url and sup.api_key:
                    return sup.api_url, sup.api_key
        except Exception:
            pass
        base_url = os.getenv("PANDORA_API_URL", self.DEFAULT_BASE_URL)
        return base_url, EnvKeys.PANDORA_API_KEY

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "PandoraClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    async def get_products(self) -> list[dict[str, Any]]:
        """Retrieve all products, stopping on an empty or repeated cursor."""
        api_url, api_key = await self._load_credentials()
        if not api_key:
            raise PandoraAPIError("PANDORA_API_KEY is not configured")

        base_url = api_url.rstrip("/")
        url = f"{base_url}/products"
        headers = {"Authorization": f"Bearer {api_key}"}
        params: dict[str, Any] = {"limit": 100}
        seen_cursors: set[str] = set()
        products: list[dict[str, Any]] = []
        self.pages_fetched = 0

        try:
            session = self._get_session()
            while True:
                async with session.get(url, headers=headers, params=params) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)

                self.pages_fetched += 1
                if not isinstance(payload, dict):
                    raise PandoraAPIError("Pandora returned an invalid response")

                items = payload.get("items")
                if not isinstance(items, list):
                    raise PandoraAPIError("Pandora returned an invalid product list")
                products.extend(item for item in items if isinstance(item, dict))

                cursor = payload.get("next_cursor")
                if cursor is None or cursor == "":
                    return products
                cursor = str(cursor)
                if cursor in seen_cursors:
                    raise PandoraAPIError("Pandora repeated a pagination cursor")
                seen_cursors.add(cursor)
                params = {"limit": 100, "cursor": cursor}
        except PandoraAPIError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise PandoraAPIError("Unable to retrieve Pandora products") from error
