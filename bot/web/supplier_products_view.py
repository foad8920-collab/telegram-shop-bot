"""Standalone admin page: browse supplier products with a dropdown."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from sqladmin import BaseView, expose
from starlette.requests import Request
from sqlalchemy import select, func as sa_func, desc

from bot.database.main import Database
from bot.database.models.main import Supplier, SupplierProduct


logger = logging.getLogger(__name__)


PER_PAGE = 50


def _fmt(value) -> str:
    if value is None:
        return ""
    try:
        return f"${Decimal(str(value)).quantize(Decimal('0.01'))}"
    except Exception:
        return str(value)


class SupplierProductsBrowserView(BaseView):
    """Browse supplier products with a supplier dropdown + search + pagination."""
    name = "تصفح منتجات الموردين"
    icon = "fa-solid fa-magnifying-glass-chart"

    @expose("/supplier-products-browser", methods=["GET"])
    async def browser(self, request: Request):
        # --- Parse query params ---
        try:
            supplier_id = int(request.query_params.get("supplier_id") or 0)
        except (ValueError, TypeError):
            supplier_id = 0

        try:
            page = max(1, int(request.query_params.get("page") or 1))
        except (ValueError, TypeError):
            page = 1

        query = (request.query_params.get("q") or "").strip()
        visible_only = request.query_params.get("visible") == "1"

        # --- Load suppliers list ---
        async with Database().session() as session:
            suppliers_result = await session.execute(
                select(Supplier).order_by(Supplier.sort_order, Supplier.id)
            )
            suppliers = list(suppliers_result.scalars().all())

        # --- Default supplier: first if not specified ---
        if supplier_id == 0 and suppliers:
            supplier_id = suppliers[0].id

        # --- Count + fetch products ---
        products = []
        total = 0
        total_pages = 1
        current_supplier = None

        if supplier_id > 0:
            async with Database().session() as session:
                current_supplier = await session.get(Supplier, supplier_id)

                base_filter = [SupplierProduct.supplier_id == supplier_id]
                if visible_only:
                    base_filter.append(SupplierProduct.is_visible == True)
                if query:
                    base_filter.append(
                        sa_func.lower(SupplierProduct.name).like(f"%{query.lower()}%")
                    )

                count_result = await session.execute(
                    select(sa_func.count(SupplierProduct.id)).where(*base_filter)
                )
                total = int(count_result.scalar() or 0)

                total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
                if page > total_pages:
                    page = total_pages

                offset = (page - 1) * PER_PAGE
                products_result = await session.execute(
                    select(SupplierProduct)
                    .where(*base_filter)
                    .order_by(SupplierProduct.id.desc())
                    .offset(offset)
                    .limit(PER_PAGE)
                )
                products = list(products_result.scalars().all())

        # --- Build product rows for template ---
        rows = []
        for p in products:
            stock = int(p.stock or 0)
            available = p.is_available and (p.is_unlimited or stock > 0)
            rows.append({
                "id": p.id,
                "external_id": p.external_id,
                "name": p.name or "",
                "category": p.category or "",
                "cost_price": _fmt(p.cost_price),
                "selling_price": _fmt(p.selling_price),
                "manual_price": _fmt(p.manual_price) if p.manual_price else "",
                "stock": ("" if p.is_unlimited else str(stock)),
                "is_visible": p.is_visible,
                "is_available": available,
            })

        # --- Build supplier dropdown options ---
        supplier_options = []
        for s in suppliers:
            supplier_options.append({
                "id": s.id,
                "name": s.display_name or s.name or f"#{s.id}",
                "icon": s.icon_emoji or "",
                "selected": (s.id == supplier_id),
            })

        # --- Build pagination range ---
        page_range = list(range(max(1, page - 2), min(total_pages, page + 2) + 1))

        context = {
            "title": "تصفح منتجات الموردين",
            "supplier_options": supplier_options,
            "supplier_id": supplier_id,
            "current_supplier": current_supplier,
            "products": rows,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "page_range": page_range,
            "query": query,
            "visible_only": visible_only,
        }

        return await self.templates.TemplateResponse(
            request,
            "supplier_products_browser.html",
            context,
        )