from .main import router as main_router
from .balance_and_payment import router as balance_and_payment_router
from .shop_and_goods import router as shop_and_goods_router
from .referral_system import router as referral_system_router
from .cart import router as cart_router
from .subscription_store import router as subscription_store_router
from .local_buy import router as local_buy_router
from .local_buy_receipts import router as local_buy_receipts_router
from .local_buy_admin import router as local_buy_admin_router
from .supplier_products import router as supplier_products_router
from .shop_search import router as shop_search_router

from aiogram import Router

router = Router()
router.include_router(main_router)
router.include_router(balance_and_payment_router)
router.include_router(shop_and_goods_router)
router.include_router(referral_system_router)
router.include_router(cart_router)
router.include_router(subscription_store_router)
router.include_router(local_buy_router)
router.include_router(local_buy_receipts_router)
router.include_router(local_buy_admin_router)
router.include_router(supplier_products_router)
router.include_router(shop_search_router)
