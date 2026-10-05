"""Synchronize StackVault supplier data into local Goods records."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from bot.database import Database
from bot.database.models import Categories, Goods
from bot.misc.services.stackvault import StackVaultClient


_FALLBACK_CATEGORY = "StackVault"


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _stackvault_name(name: str, stackvault_id: str, max_length: int | None) -> str:
    """Return a bounded name that remains distinct from a manual product."""
    suffix = f" ({stackvault_id})"
    if max_length is None:
        return f"{name}{suffix}"
    if len(suffix) >= max_length:
        return suffix[:max_length]
    return f"{name[: max_length - len(suffix)]}{suffix}"


async def sync_stackvault_products(
    client: StackVaultClient | None = None,
) -> dict[str, int]:
    """Upsert StackVault products into Goods using the supplier id as the key.

    Only supplier fields are updated for existing records. Goods with a NULL
    ``stackvault_product_id`` are never selected by this synchronization.
    """
    if client is None:
        async with StackVaultClient() as owned_client:
            products = await owned_client.get_products()
    else:
        products = await client.get_products()
    created = 0
    updated = 0
    skipped = 0

    async with Database().session() as session:
        processed_stackvault_ids: set[str] = set()
        pending_names: set[str] = set()
        name_length = getattr(Goods.__table__.c.name.type, "length", None)
        category = (
            await session.execute(
                select(Categories).where(Categories.name == _FALLBACK_CATEGORY)
            )
        ).scalars().first()
        if category is None:
            category = Categories(name=_FALLBACK_CATEGORY)
            session.add(category)
            await session.flush()

        for product in products:
            product_id = product.get("id")
            name = str(product.get("name") or "").strip()
            if product_id is None or not name:
                skipped += 1
                continue

            stackvault_id = str(product_id)
            if stackvault_id in processed_stackvault_ids:
                skipped += 1
                continue
            processed_stackvault_ids.add(stackvault_id)
            supplier_price = _decimal(product.get("price"))
            supplier_stock = _int(product.get("stock"))
            supplier_in_stock = product.get("in_stock")
            if supplier_in_stock is not None and not isinstance(supplier_in_stock, bool):
                skipped += 1
                continue

            goods = (
                await session.execute(
                    select(Goods).where(Goods.stackvault_product_id == stackvault_id)
                )
            ).scalars().first()

            if goods is None:
                candidate_name = name
                if candidate_name in pending_names or (
                    await session.execute(
                        select(Goods).where(Goods.name == candidate_name)
                    )
                ).scalars().first() is not None:
                    candidate_name = _stackvault_name(name, stackvault_id, name_length)
                    suffix_index = 2
                    while candidate_name in pending_names or (
                        await session.execute(
                            select(Goods).where(Goods.name == candidate_name)
                        )
                    ).scalars().first() is not None:
                        candidate_name = _stackvault_name(
                            name,
                            f"{stackvault_id}-{suffix_index}",
                            name_length,
                        )
                        suffix_index += 1
                # A neutral local price is required by the existing schema;
                # supplier_price is intentionally kept separate from sale price.
                goods = Goods(
                    name=candidate_name,
                    price=Decimal("0.00"),
                    description=str(product.get("description") or name),
                    category_id=category.id,
                    stackvault_product_id=stackvault_id,
                    supplier_price=supplier_price,
                    supplier_stock=supplier_stock,
                    supplier_in_stock=supplier_in_stock,
                    stackvault_enabled=True,
                )
                session.add(goods)
                pending_names.add(candidate_name)
                created += 1
            else:
                goods.supplier_price = supplier_price
                goods.supplier_stock = supplier_stock
                goods.supplier_in_stock = supplier_in_stock
                updated += 1

        # Persist the supplier quantities explicitly. The synchronization must
        # not report success while the session still has uncommitted Goods rows.
        await session.flush()
        await session.commit()

    return {"created": created, "updated": updated, "skipped": skipped}
