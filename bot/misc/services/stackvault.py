"""Small read-only client for the StackVault reseller catalog."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from bot.misc import EnvKeys

logger = logging.getLogger(__name__)


class StackVaultAPIError(RuntimeError):
    """The StackVault catalog could not be retrieved or parsed."""


class StackVaultClient:
    """Fetch the reseller catalog without coupling it to payment flows."""

    _timeout = aiohttp.ClientTimeout(total=20)

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        """Close the reusable HTTP session owned by this client."""
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "StackVaultClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    async def get_products(self) -> list[dict[str, Any]]:
        if not EnvKeys.STACKVAULT_API_KEY:
            raise StackVaultAPIError("STACKVAULT_API_KEY is not configured")

        url = f"{EnvKeys.STACKVAULT_API_URL.rstrip('/')}/products"
        headers = {"Authorization": f"Bearer {EnvKeys.STACKVAULT_API_KEY}"}
        try:
            session = self._get_session()
            async with session.get(url, headers=headers) as response:
                if response.status >= 400:
                    response_body = await response.text()
                    logger.error(
                        "StackVault HTTP error: status=%s url=%s response=%s",
                        response.status,
                        url,
                        response_body,
                    )
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            logger.error("StackVault request failed: %s", error, exc_info=True)
            raise StackVaultAPIError("Unable to retrieve StackVault products") from error

        if not isinstance(payload, dict) or not payload.get("ok"):
            logger.error("StackVault returned an unsuccessful payload: %r", payload)
            raise StackVaultAPIError("StackVault returned an unsuccessful response")

        products = payload.get("products", [])
        if not isinstance(products, list):
            logger.error("StackVault returned an invalid product list: %r", payload)
            raise StackVaultAPIError("StackVault returned an invalid product list")

        return [product for product in products if isinstance(product, dict)]

    async def create_order(
        self,
        product_id: str,
        quantity: int = 1,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not EnvKeys.STACKVAULT_API_KEY:
            raise StackVaultAPIError("STACKVAULT_API_KEY is not configured")

        url = f"{EnvKeys.STACKVAULT_API_URL.rstrip('/')}/orders"
        headers = {"Authorization": f"Bearer {EnvKeys.STACKVAULT_API_KEY}"}
        body: dict[str, Any] = {"product_id": product_id, "quantity": quantity}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key

        try:
            session = self._get_session()
            async with session.post(url, headers=headers, json=body) as response:
                if response.status >= 400:
                    response_body = await response.text()
                    logger.error(
                        "StackVault order HTTP error: status=%s url=%s response=%s",
                        response.status,
                        url,
                        response_body,
                    )
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            status = getattr(error, "status", None)
            suffix = f" (HTTP {status})" if status else ""
            logger.error("StackVault order request failed: %s", error, exc_info=True)
            raise StackVaultAPIError(f"Unable to create StackVault order{suffix}") from error

        if not isinstance(payload, dict) or not payload.get("ok"):
            logger.error("StackVault returned an unsuccessful order payload: %r", payload)
            raise StackVaultAPIError("StackVault returned an unsuccessful order response")

        return payload

    async def get_order(self, order_id: str) -> dict[str, Any]:
        if not EnvKeys.STACKVAULT_API_KEY:
            raise StackVaultAPIError("STACKVAULT_API_KEY is not configured")

        url = f"{EnvKeys.STACKVAULT_API_URL.rstrip('/')}/orders/{order_id}"
        headers = {"Authorization": f"Bearer {EnvKeys.STACKVAULT_API_KEY}"}

        try:
            session = self._get_session()
            async with session.get(url, headers=headers) as response:
                if response.status >= 400:
                    response_body = await response.text()
                    logger.error(
                        "StackVault order lookup HTTP error: status=%s url=%s response=%s",
                        response.status,
                        url,
                        response_body,
                    )
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            status = getattr(error, "status", None)
            suffix = f" (HTTP {status})" if status else ""
            logger.error("StackVault order lookup failed: %s", error, exc_info=True)
            raise StackVaultAPIError(f"Unable to retrieve StackVault order{suffix}") from error

        if not isinstance(payload, dict) or not payload.get("ok"):
            logger.error("StackVault returned an unsuccessful order payload: %r", payload)
            raise StackVaultAPIError("StackVault returned an unsuccessful order response")

        return payload

    async def get_categories(self) -> list[dict[str, Any]]:
        """Fetch StackVault categories when the API exposes a categories route."""
        if not EnvKeys.STACKVAULT_API_KEY:
            raise StackVaultAPIError("STACKVAULT_API_KEY is not configured")

        url = f"{EnvKeys.STACKVAULT_API_URL.rstrip('/')}/categories"
        headers = {"Authorization": f"Bearer {EnvKeys.STACKVAULT_API_KEY}"}
        try:
            session = self._get_session()
            async with session.get(url, headers=headers) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error(
                        "StackVault categories HTTP error: status=%s url=%s response=%s",
                        response.status, url, body,
                    )
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            logger.error("StackVault categories request failed: %s", error, exc_info=True)
            raise StackVaultAPIError("Unable to retrieve StackVault categories") from error

        categories = payload.get("categories", []) if isinstance(payload, dict) else []
        if not isinstance(categories, list):
            raise StackVaultAPIError("StackVault returned an invalid category list")
        return [category for category in categories if isinstance(category, dict)]
