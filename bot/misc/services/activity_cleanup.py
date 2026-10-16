"""Background scheduler to clean up old user activities."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


# احتفظ بالسجلات لمدة 30 يومًا
RETENTION_DAYS = 30

# الفحص كل 6 ساعات
CHECK_INTERVAL_SECONDS = 6 * 3600


class ActivityCleanupScheduler:
    """Background task to remove activities older than RETENTION_DAYS."""

    def __init__(self, retention_days: int = RETENTION_DAYS):
        self.retention_days = retention_days
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            logger.info("ActivityCleanupScheduler already running")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="activity_cleanup_scheduler")
        logger.info(
            "ActivityCleanupScheduler started (retention=%dd, check every %ds)",
            self.retention_days, CHECK_INTERVAL_SECONDS,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        logger.info("ActivityCleanupScheduler stopped")

    async def _run(self) -> None:
        # تشغيل أول تنظيف بعد 60 ثانية من الإقلاع
        await asyncio.sleep(60)
        while not self._stop_event.is_set():
            try:
                await self._cleanup_once()
            except Exception as e:
                logger.exception("ActivityCleanupScheduler error: %s", e)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
                break
            except asyncio.TimeoutError:
                continue

    async def _cleanup_once(self) -> None:
        from bot.database.methods.activity import cleanup_old_activities
        deleted = await cleanup_old_activities(days=self.retention_days)
        if deleted:
            logger.info("ActivityCleanup: removed %d old rows", deleted)


# singleton
_scheduler: ActivityCleanupScheduler | None = None


def get_activity_cleanup_scheduler() -> ActivityCleanupScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = ActivityCleanupScheduler()
    return _scheduler
