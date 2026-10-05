from aiogram import Router, F
from aiogram.types import CallbackQuery
import aiohttp
from sqlalchemy import select

from bot.misc.env import EnvKeys
from bot.database import Database
from bot.database.models import Categories, Goods
from bot.misc.services import StackVaultClient

router = Router()


@router.callback_query(F.data == "stackvault_test")
async def stackvault_test_handler(call: CallbackQuery):
    try:
        # StackVault exposes the catalog through /products only. Categories are
        # extracted from each product instead of calling a non-existent route.
        categories = []
        async with StackVaultClient() as client:
            products = await client.get_products()

        # Also accept category objects embedded in products, so nested products
        # are never lost when the standalone endpoint is unavailable.
        for product in products:
            category = product.get("category")
            if isinstance(category, dict):
                categories.append(category)
            elif product.get("category_id") is not None or product.get("category_name"):
                categories.append({
                    "id": product.get("category_id"),
                    "name": product.get("category_name") or str(product.get("category_id")),
                })

        category_by_external: dict[str, Categories] = {}
        async with Database().session() as session:
            # Upsert categories first; products are inserted only after this map
            # is complete, preventing foreign-key failures.
            for raw in categories:
                name = str(raw.get("name") or raw.get("title") or "").strip()
                if not name:
                    continue
                category = (await session.execute(
                    select(Categories).where(Categories.name == name)
                )).scalars().first()
                if category is None:
                    category = Categories(name=name)
                    session.add(category)
                    await session.flush()
                external_id = raw.get("id")
                if external_id is not None:
                    category_by_external[str(external_id)] = category
                category_by_external.setdefault(name.casefold(), category)

            synced = 0
            for product in products:
                name = str(product.get("name") or "").strip()
                if not name:
                    continue
                raw_category = product.get("category")
                external_id = product.get("category_id")
                category_name = product.get("category_name")
                if isinstance(raw_category, dict):
                    external_id = raw_category.get("id", external_id)
                    category_name = raw_category.get("name") or raw_category.get("title") or category_name
                category = category_by_external.get(str(external_id)) if external_id is not None else None
                if category is None and category_name:
                    category = category_by_external.get(str(category_name).casefold())
                if category is None:
                    # Root products get a stable local fallback category.
                    fallback = "StackVault"
                    category = category_by_external.get(fallback.casefold())
                    if category is None:
                        category = Categories(name=fallback)
                        session.add(category)
                        await session.flush()
                        category_by_external[fallback.casefold()] = category

                price = product.get("price") or product.get("selling_price") or 0
                description = str(product.get("description") or product.get("name") or name)
                goods = (await session.execute(
                    select(Goods).where(Goods.name == name)
                )).scalars().first()
                if goods is None:
                    session.add(Goods(name=name, price=price, description=description, category_id=category.id))
                else:
                    goods.price = price
                    goods.description = description
                    goods.category_id = category.id
                synced += 1

        text = "🔌 StackVault Products\n\n"
        for product in products[:10]:
            text += (
                f"📦 {product['name']}\n"
                f"💵 {product['price']}$\n"
                f"📊 Stock: {product['stock']}\n\n"
            )

        await call.message.answer(f"✅ تمت مزامنة {synced} منتجاً و{len(category_by_external)} قسماً.\n\n" + text)

    except Exception as e:
        await call.message.answer(
            f"❌ Error:\n{e}"
        )
