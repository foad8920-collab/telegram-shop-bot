"""Middleware that exposes the global Dispatcher to all handlers.

Why: some flows (e.g. admin approves an order and the *buyer* needs to enter
an FSM state) require setting FSM state for a *different* user than the one
who triggered the current update. The only way to do that in aiogram 3 is to
call ``dispatcher.fsm.set_state(StorageKey(...))`` directly. Handlers normally
have access only to the current update; this middleware injects the
dispatcher into the handler data dict under the key ``dispatcher``.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class DispatcherMiddleware(BaseMiddleware):
    """Injects the Dispatcher instance into handler data as `dispatcher`."""

    def __init__(self, dispatcher) -> None:
        super().__init__()
        self._dispatcher = dispatcher

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["dispatcher"] = self._dispatcher
        return await handler(event, data)
