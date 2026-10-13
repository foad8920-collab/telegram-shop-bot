"""Generic supplier client - works with any REST API.

Supports common response shapes:
- Direct list: [ {...}, {...} ]
- Wrapped: {"data": [...]}, {"products": [...]}, {"items": [...]}, {"result": [...]}
- Nested: {"data": {"products": [...]}}

And common field names for products.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class GenericSupplierError(RuntimeError):
    """Raised when a generic supplier API call fails."""


class GenericSupplierClient:
    """Universal client for suppliers without a dedicated class."""

    # Possible response keys containing the product list
    PRODUCTS_KEYS = ("products", "data", "items", "result", "list", "goods", "catalog")

    # Possible field names for product attributes
    ID_KEYS = ("id", "product_id", "external_id", "sku", "code")
    NAME_KEYS = ("name", "title", "product_name", "label")
    PRICE_KEYS = ("price", "cost", "cost_price", "unit_price", "base_price", "supplier_price")
    STOCK_KEYS = ("stock", "quantity", "available_stock", "count", "remaining", "qty", "inventory")
    DESC_KEYS = ("description", "desc", "details", "info", "summary")
    IMAGE_KEYS = ("image", "image_url", "photo", "thumbnail", "picture", "img")
    CATEGORY_KEYS = ("category", "category_name", "group", "type", "section")

    _timeout = aiohttp.ClientTimeout(total=30)

    def __init__(self, api_url: str, api_key: str = "") -> None:
        self.api_url = (api_url or "").rstrip("/")
        self.api_key = api_key or ""
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "GenericSupplierClient":
        headers = {"Accept": "application/json"}
        if self.api_key:
            # Provide common auth header variants for various APIs
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
            headers["X-Reseller-Key"] = self.api_key
            headers["api-key"] = self.api_key
            headers["apikey"] = self.api_key
        self._session = aiohttp.ClientSession(headers=headers, timeout=self._timeout)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    async def get_products(self) -> list[dict]:
        """Fetch and normalize products from the supplier API."""
        if not self.api_url:
            raise GenericSupplierError("API URL is empty")
        if not self._session:
            raise GenericSupplierError("Client not initialized")

        url = self.api_url
        if not url.endswith("/products"):
            url = f"{url}/products"

        try:
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise GenericSupplierError(
                        f"HTTP {resp.status}: {text[:200]}"
                    )
                payload = await resp.json()
        except aiohttp.ClientError as e:
            raise GenericSupplierError(f"Network error: {e}") from e
        except Exception as e:
            raise GenericSupplierError(f"Unexpected error: {e}") from e

        products = self._extract_products(payload)
        normalized = [self._normalize(p) for p in products if isinstance(p, dict)]
        logger.info("Generic supplier returned %d products from %s", len(normalized), url)
        return normalized

    # --- Helpers ---

    def _extract_products(self, payload: Any) -> list[dict]:
        """Find the product list inside any common response shape."""
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        # Try each known key
        for key in self.PRODUCTS_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for nested_key in self.PRODUCTS_KEYS:
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        return nested
        return []

    @staticmethod
    def _first(d: dict, keys: tuple) -> Any:
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return None

    def _normalize(self, product: dict) -> dict:
        """Normalize field names to match the bot's expected schema."""
        price = self._first(product, self.PRICE_KEYS) or 0
        stock = self._first(product, self.STOCK_KEYS) or 0
        return {
            "id": str(self._first(product, self.ID_KEYS) or ""),
            "name": str(self._first(product, self.NAME_KEYS) or "Unknown"),
            "price": price,
            "stock": stock,
            "description": str(self._first(product, self.DESC_KEYS) or ""),
            "image_url": str(self._first(product, self.IMAGE_KEYS) or ""),
            "category": str(self._first(product, self.CATEGORY_KEYS) or ""),
            "raw": product,
        }
