"""تشغيل بوت المتجر وبوت الدعم الفني معًا."""
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv(encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _run_shop_bot():
    """تشغيل بوت المتجر الرئيسي."""
    from bot import start_bot
    await start_bot()


async def _run_support_bot():
    """تشغيل بوت الدعم الفني."""
    try:
        from support_bot import main as support_main
        await support_main()
    except Exception as e:
        logger.exception("Support bot failed: %s", e)


async def _run_subworld_bot():
    """تشغيل بوت SubWorld (البوت الرئيسي)."""
    try:
        from subworld import main as subworld_main
        await subworld_main()
    except Exception as e:
        logger.exception("SubWorld bot failed: %s", e)

async def main():
    """تشغيل البوتات معًا بشكل متوازي (SubWorld هو البوت الرئيسي)."""
    logger.info("Starting bots together (SubWorld is the main bot)...")
    await asyncio.gather(
        _run_subworld_bot(),
        _run_shop_bot(),
        _run_support_bot(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bots stopped.")
