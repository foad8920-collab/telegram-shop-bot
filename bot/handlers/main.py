from aiogram import Dispatcher

from bot.handlers.admin import router as admin_router
from bot.handlers.other import router as other_router
from bot.handlers.user import router as user_router
from bot.handlers.user.channel_member import router as channel_member_router


def register_all_handlers(dp: Dispatcher) -> None:
    dp.include_router(admin_router)
    dp.include_router(other_router)
    dp.include_router(user_router)
    dp.include_router(channel_member_router)
