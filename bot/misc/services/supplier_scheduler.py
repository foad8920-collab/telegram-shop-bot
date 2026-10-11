"""Background scheduler for supplier auto-sync."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from bot.database.main import Database
from bot.database.models.main import Supplier
from bot.misc.services.supplier_sync import sync_supplier


logger = logging.getLogger(__name__)


# How often the scheduler checks (in seconds)
CHECK_INTERVAL_SECONDS = 300  # 5 minutes


class SupplierSyncScheduler:
    """Background task that periodically syncs suppliers."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Start the background scheduler (idempotent)."""
        if self._task is not None and not self._task.done():
            logger.info("SupplierSyncScheduler already running")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="supplier_sync_scheduler")
        logger.info("SupplierSyncScheduler started (check every %ss)", CHECK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        logger.info("SupplierSyncScheduler stopped")

    async def _run(self) -> None:
        """Main loop."""
        # Wait a bit at startup so bot can fully boot
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=30)
            return
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            try:
                await self._check_and_sync()
            except Exception as e:
                logger.exception("SupplierSyncScheduler iteration failed: %s", e)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
                return  # stop requested
            except asyncio.TimeoutError:
                pass

    async def _check_and_sync(self) -> None:
        """Find suppliers that are due for a sync and run them."""
        now = datetime.now(timezone.utc)

        async with Database().session() as session:
            result = await session.execute(
                select(Supplier).where(
                    Supplier.sync_enabled == True,  # noqa: E712
                    Supplier.is_active == True,      # noqa: E712
                )
            )
            suppliers = list(result.scalars().all())

        for supplier in suppliers:
            api_url = (supplier.api_url or "").strip()
            if api_url.startswith("local://"):
                continue  # local suppliers  skip

            interval_min = int(supplier.sync_interval_minutes or 240)
            if interval_min <= 0:
                continue  # auto-sync disabled for this supplier

            last = supplier.last_sync_at
            if last is None:
                due = True
            else:
                delta = (now - last).total_seconds() / 60.0
                due = delta >= interval_min

            if not due:
                continue

            logger.info(
                "Auto-syncing supplier %s (%s)  last sync %s min ago (interval=%s)",
                supplier.id, supplier.name,
                round((now - last).total_seconds() / 60.0) if last else "never",
                interval_min,
            )

            try:
                report = await sync_supplier(supplier.id, manual=False)
                logger.info("Auto-sync result for %s: %s", supplier.id, report)
            except Exception as e:
                logger.exception("Auto-sync failed for supplier %s: %s", supplier.id, e)


# Global singleton
_scheduler: SupplierSyncScheduler | None = None


def get_scheduler() -> SupplierSyncScheduler:
    """Return the global scheduler singleton."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SupplierSyncScheduler()
    return _scheduler
