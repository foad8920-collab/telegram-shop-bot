"""Customer catalog (v1, read-only) for the Yemen Subscriptions web store.

Read-only: SELECT queries only, no auth, no writes, no session changes.
Mounted by create_admin_app() in bot/web/admin.py via ``shop_routes``
(imported inside create_admin_app, same pattern as ``export_routes``).

URL scheme (mirrors the bot's callback namespaces, because IDs collide
across tables -- LocalSubscription and SupplierProduct both start at 1):
  GET /shop                                        catalog (Arabic, RTL)
  GET /shop/product/local/{sub_id}                 LocalSubscription detail
  GET /shop/product/supplier/{supplier_id}/{product_id}  SupplierProduct detail

Features (v1):
  - Grouped default view: local section + one section per active supplier,
    24 cards per section with a "show more" link to a flat view.
  - Flat mode (any search / filter / sort active): one list, 24 per page.
  - Smart Arabic search: alef/hamza variants, teh-marbuta, diacritics and
    tatweel normalized in Python (SQL LIKE cannot do this), searched in
    name AND description.
  - Category filter (?category=), sort (?sort=price_asc|price_desc|name).
  - "New" badge (created_at within 7 days), delivery-type tag for local
    products, last-sync line for supplier products.
  - Favorites in the browser (localStorage) -- no account needed.
  - Light/dark theme toggle (localStorage) and light CSS motion
    (hover zoom/lift, staggered fade-in, shine on the "new" badge),
    disabled under prefers-reduced-motion.
  - Prices: LocalSubscription follows shop_and_goods.local_sub_details_handler
    (shop_and_goods.py:729-744) exactly; SupplierProduct follows
    supplier_products.supplier_product_detail_handler. No pricing logic is
    changed here -- get_currency_rates / convert_price from
    bot.database.methods.transactions are reused as-is.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from urllib.parse import urlencode

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from bot.database.main import Database
from bot.database.methods.transactions import convert_price, get_currency_rates
from bot.database.models.main import LocalSubscription, Supplier, SupplierProduct

logger = logging.getLogger(__name__)

# Same templates dir the admin panel already uses (admin.py:1107, 2264).
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "templates")
)

# Bot's own low-stock threshold: admin.py:1996 low_stock event (0 < stock <= 5).
LOW_STOCK_MAX = 5
MIN_SEARCH_LEN = 2
MAX_SEARCH_LEN = 50
PAGE_SIZE = 24
_GROUP_LIMIT = 25  # fetch one extra to know if a "show more" link is needed
_SEARCH_LIMIT = 1000  # per section when searching (Python-side normalization)
NEW_DAYS = 7

_DELIVERY_LABELS = {"codes": "أكواد", "invite": "دعوة", "manual": "يدوي"}

# Smart Arabic search normalization: alef forms -> ا, teh-marbuta -> ه,
# alef-maksura -> ي; diacritics (064B-0652, 0670) and tatweel (0640) stripped.
_AR_TRANSLATE = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي"})
_DIACRITICS_RE = re.compile(r"[\u064b-\u0652\u0670\u0640]")


def _normalize_ar(text):
    """Normalize Arabic (and lowercase Latin) for search matching."""
    text = _DIACRITICS_RE.sub("", str(text or ""))
    text = text.translate(_AR_TRANSLATE)
    return " ".join(text.split()).lower()


def _matches_query(name, description_full, tokens):
    hay = _normalize_ar((name or "") + " " + (description_full or ""))
    return all(t in hay for t in tokens)


def _is_new(created_at):
    """True if created within the last NEW_DAYS days (both models have it)."""
    if not created_at:
        return False
    try:
        dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt <= timedelta(days=NEW_DAYS)
    except Exception:
        return False


def _fmt_dt(dt):
    """Plain 'YYYY-MM-DD HH:MM' or None. Stored datetimes are tz-aware."""
    if not dt:
        return None
    try:
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _shorten(text, max_len=160):
    """Card excerpt. Same idea as shop_and_goods._shorten (default 45 there)."""
    text = str(text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _local_prices(sub, rates):
    """Mirror shop_and_goods.py:729-744 exactly. Values are display strings."""
    if getattr(sub, "price", None) is None:
        return {"usd": None, "sar": None, "yer": None}
    try:
        prices = convert_price(sub.price, rates)
        usd = f"${prices['usd']}"
        sar = f"{prices['sar']} ريال سعودي"
        yer = f"{prices['yer']} ريال يمني"
        if getattr(sub, "price_yer", None) is not None:
            yer = f"{int(sub.price_yer)} ريال يمني"
            sar_value = (
                Decimal(str(int(sub.price_yer))) / Decimal(str(rates["sar_to_yer"]))
            ).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
            sar = f"{sar_value} ريال سعودي"
        return {"usd": usd, "sar": sar, "yer": yer}
    except Exception:
        # Same fallback as the bot handler: USD only.
        return {"usd": f"${sub.price}", "sar": None, "yer": None}


def _supplier_prices(selling_price, rates):
    """Mirror supplier_products.py:510,530. None selling_price -> unavailable.

    The bot detail shows $0 for None (format_price_arabic_async(... or 0));
    the catalog shows 'unavailable' instead -- $0 would mislead a buyer.
    """
    if selling_price is None:
        return {"usd": None, "sar": None, "yer": None}
    try:
        prices = convert_price(selling_price, rates)
        return {
            "usd": f"${prices['usd']}",
            "sar": f"{prices['sar']} ريال سعودي",
            "yer": f"{prices['yer']} ريال يمني",
        }
    except Exception:
        return {"usd": f"${selling_price}", "sar": None, "yer": None}


def _supplier_stock(prod):
    """Mirror supplier_products.py:511,514-517 (available + stock text)."""
    stock = int(prod.stock or 0)
    available = bool(prod.is_available) and (bool(prod.is_unlimited) or stock > 0)
    if not available:
        return {"state": "out", "badge": "غير متوفر", "ribbon": None,
                "stock_text": "نفدت الكمية"}
    if prod.is_unlimited:
        return {"state": "ok", "badge": "غير محدود", "ribbon": None,
                "stock_text": "غير محدود"}
    ribbon = "مخزون قليل" if stock <= LOW_STOCK_MAX else None
    return {"state": "low" if ribbon else "ok", "badge": f"المتوفر {stock}",
            "ribbon": ribbon, "stock_text": f"المتوفر {stock}"}


def _local_stock(qty):
    """Mirror shop_and_goods.py:620-631 (sellable_quantity > 0 = available)."""
    qty = int(qty or 0)
    if qty <= 0:
        return {"state": "out", "badge": "غير متوفر", "ribbon": None,
                "stock_text": "نفدت الكمية"}
    ribbon = "مخزون قليل" if qty <= LOW_STOCK_MAX else None
    return {"state": "low" if ribbon else "ok", "badge": f"المتوفر {qty}",
            "ribbon": ribbon, "stock_text": f"المتوفر {qty}"}


def _supplier_card(prod, rates, supplier_name):
    return {
        "kind": "supplier",
        "id": prod.id,
        "supplier_id": prod.supplier_id,
        "supplier_name": supplier_name,
        "name": prod.name,
        "category": prod.category or "",
        "description": _shorten(prod.description, 160),
        "description_full": prod.description or "",
        "image_url": (
            prod.image_url
            if isinstance(prod.image_url, str) and prod.image_url.startswith("http")
            else None
        ),
        "prices": _supplier_prices(prod.selling_price, rates),
        "usd_value": prod.selling_price,
        "stock": _supplier_stock(prod),
        "is_new": _is_new(getattr(prod, "created_at", None)),
        "last_synced": _fmt_dt(getattr(prod, "last_synced_at", None)),
        "delivery": None,
        "fav_key": f"supplier:{prod.id}",
        "url": f"/shop/product/supplier/{prod.supplier_id}/{prod.id}",
    }


def _local_card(sub, rates):
    qty = int(getattr(sub, "sellable_quantity", None) or 0)
    return {
        "kind": "local",
        "id": sub.id,
        "name": sub.name,
        "category": sub.category or "عام",
        "description": _shorten(sub.description, 160),
        "description_full": sub.description or "",
        "image_url": None,
        "supplier_name": None,
        "prices": _local_prices(sub, rates),
        "usd_value": sub.price,
        "stock": _local_stock(qty),
        "is_new": _is_new(getattr(sub, "created_at", None)),
        "last_synced": None,
        "delivery": _DELIVERY_LABELS.get(
            (sub.delivery_type or "codes").lower(), "أكواد"),
        "fav_key": f"local:{sub.id}",
        "url": f"/shop/product/local/{sub.id}",
    }


def _sort_cards(cards, sort):
    """Sort flat results. Priceless cards always go last."""
    if sort == "name":
        return sorted(cards, key=lambda c: _normalize_ar(c["name"]))
    if sort in ("price_asc", "price_desc"):
        priced = [c for c in cards if c["usd_value"] is not None]
        unpriced = [c for c in cards if c["usd_value"] is None]
        priced.sort(key=lambda c: c["usd_value"],
                    reverse=(sort == "price_desc"))
        return priced + unpriced
    return cards


def _not_found(request, message):
    return templates.TemplateResponse(
        request, "shop/detail.html",
        {"title": "غير موجود", "item": None, "error": message},
        status_code=404,
    )


async def shop_catalog(request: Request):
    q_raw = (request.query_params.get("q") or "").strip()
    supplier_filter = request.query_params.get("supplier") or ""
    category_filter = (request.query_params.get("category") or "").strip()
    sort = request.query_params.get("sort") or ""
    section = request.query_params.get("section") or ""
    available_only = request.query_params.get("available") == "1"
    search_error = None
    q = q_raw
    if q and (len(q) < MIN_SEARCH_LEN or len(q) > MAX_SEARCH_LEN):
        search_error = "كلمة البحث يجب أن تكون بين حرفين و50 حرفاً."
        q = ""
    tokens = _normalize_ar(q).split() if q else []
    try:
        supplier_id = int(supplier_filter) if supplier_filter else None
    except ValueError:
        supplier_id = None
    if sort not in ("price_asc", "price_desc", "name"):
        sort = ""
    if section != "local":
        section = ""
    try:
        page = int(request.query_params.get("page") or "1")
    except ValueError:
        page = 1
    page = max(1, page)

    # Any search/filter/sort -> flat paginated list; otherwise grouped view.
    flat = bool(q or supplier_id is not None or category_filter or sort
                or available_only or section)

    rates = await get_currency_rates()
    local_items, moved_items, supplier_groups, suppliers, categories = [], [], [], [], []
    flat_items, pagination = [], None
    has_more_local = False
    try:
        async with Database().session() as session:
            # Active suppliers, bot shop-menu order (shop_and_goods.py:488).
            sup_res = await session.execute(
                select(Supplier)
                .where(Supplier.is_active == True)  # noqa: E712
                .order_by(Supplier.sort_order, Supplier.id)
            )
            suppliers = [
                {"id": s.id, "name": s.display_name or s.name}
                for s in sup_res.scalars().all()
            ]
            supplier_names = {s["id"]: s["name"] for s in suppliers}

            # Distinct categories actually present (for the filter dropdown).
            cat_local = await session.execute(
                select(LocalSubscription.category)
                .where(LocalSubscription.is_active == True)  # noqa: E712
                .distinct()
            )
            cat_sup = await session.execute(
                select(SupplierProduct.category)
                .where(
                    SupplierProduct.is_visible == True,  # noqa: E712
                    SupplierProduct.category.is_not(None),
                )
                .distinct()
            )
            categories = sorted(
                {c for (c,) in cat_local.all() if c}
                | {c for (c,) in cat_sup.all() if c}
            )

            if flat:
                cards = []
                if supplier_id is None:
                    # --- Local subscriptions (shop_and_goods.py:660-667).
                    local_q = (
                        select(LocalSubscription)
                        .options(selectinload(LocalSubscription.codes))
                        .where(LocalSubscription.is_active == True)  # noqa: E712
                        .order_by(LocalSubscription.id)
                        .limit(_SEARCH_LIMIT)
                    )
                    if category_filter:
                        local_q = local_q.where(
                            LocalSubscription.category == category_filter)
                    for sub in (await session.execute(local_q)).scalars().all():
                        cards.append(_local_card(sub, rates))

                    # --- Supplier products moved to local (shop_and_goods.py:668-673).
                    moved_q = (
                        select(SupplierProduct)
                        .where(
                            SupplierProduct.show_in_local == True,  # noqa: E712
                            SupplierProduct.is_visible == True,  # noqa: E712
                        )
                        .order_by(SupplierProduct.id)
                        .limit(_SEARCH_LIMIT)
                    )
                    if category_filter:
                        moved_q = moved_q.where(
                            SupplierProduct.category == category_filter)
                    for prod in (await session.execute(moved_q)).scalars().all():
                        cards.append(_supplier_card(
                            prod, rates, supplier_names.get(prod.supplier_id)))

                if section != "local":
                    # --- Supplier store (one query, all suppliers or one).
                    sup_q = (
                        select(SupplierProduct)
                        .where(
                            SupplierProduct.is_visible == True,  # noqa: E712
                            SupplierProduct.show_in_local == False,  # noqa: E712
                        )
                        .order_by(
                            SupplierProduct.supplier_id,
                            SupplierProduct.is_available.desc(),
                            SupplierProduct.selling_price.asc(),
                        )
                        .limit(_SEARCH_LIMIT)
                    )
                    if supplier_id is not None:
                        sup_q = sup_q.where(
                            SupplierProduct.supplier_id == supplier_id)
                    if category_filter:
                        sup_q = sup_q.where(
                            SupplierProduct.category == category_filter)
                    for prod in (await session.execute(sup_q)).scalars().all():
                        cards.append(_supplier_card(
                            prod, rates, supplier_names.get(prod.supplier_id)))

                # Smart Arabic search runs in Python (SQL LIKE cannot
                # normalize alef/hamza/teh-marbuta/diacritics).
                if tokens:
                    cards = [
                        c for c in cards
                        if _matches_query(c["name"], c["description_full"], tokens)
                    ]
                if available_only:
                    cards = [c for c in cards if c["stock"]["state"] != "out"]
                cards = _sort_cards(cards, sort)
                total = len(cards)
                pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                page = min(page, pages)
                flat_items = cards[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
                if total > 0:
                    def _page_url(p):
                        params = dict(request.query_params)
                        params["page"] = str(p)
                        return "/shop?" + urlencode(params)

                    pagination = {
                        "page": page, "pages": pages, "total": total,
                        "prev_url": _page_url(page - 1) if page > 1 else None,
                        "next_url": _page_url(page + 1) if page < pages else None,
                    }
            else:
                # --- Grouped default view: 24 cards per section.
                local_q = (
                    select(LocalSubscription)
                    .options(selectinload(LocalSubscription.codes))
                    .where(LocalSubscription.is_active == True)  # noqa: E712
                    .order_by(LocalSubscription.id)
                    .limit(_GROUP_LIMIT)
                )
                moved_q = (
                    select(SupplierProduct)
                    .where(
                        SupplierProduct.show_in_local == True,  # noqa: E712
                        SupplierProduct.is_visible == True,  # noqa: E712
                    )
                    .order_by(SupplierProduct.id)
                    .limit(_GROUP_LIMIT)
                )
                combined = [
                    _local_card(s, rates)
                    for s in (await session.execute(local_q)).scalars().all()
                ]
                combined += [
                    _supplier_card(p, rates, supplier_names.get(p.supplier_id))
                    for p in (await session.execute(moved_q)).scalars().all()
                ]
                has_more_local = len(combined) > PAGE_SIZE
                local_items = combined[:PAGE_SIZE]

                for sup in suppliers:
                    prod_q = (
                        select(SupplierProduct)
                        .where(
                            SupplierProduct.supplier_id == sup["id"],
                            SupplierProduct.is_visible == True,  # noqa: E712
                            SupplierProduct.show_in_local == False,  # noqa: E712
                        )
                        .order_by(
                            SupplierProduct.is_available.desc(),
                            SupplierProduct.selling_price.asc(),
                        )
                        .limit(_GROUP_LIMIT)
                    )
                    prods = (await session.execute(prod_q)).scalars().all()
                    if prods:
                        supplier_groups.append({
                            "supplier": sup,
                            "products": [
                                _supplier_card(p, rates, sup["name"])
                                for p in prods[:PAGE_SIZE]
                            ],
                            "has_more": len(prods) > PAGE_SIZE,
                        })
    except Exception:
        logger.exception("shop catalog failed")
        return templates.TemplateResponse(
            request, "shop/catalog.html",
            {"title": "المتجر", "error": "تعذّر تحميل الكتالوج. حاول لاحقاً.",
             "local_items": [], "moved_items": [], "supplier_groups": [],
             "suppliers": [], "categories": [], "q": q,
             "supplier_filter": supplier_id, "category_filter": "",
             "sort": "", "section": "", "available_only": available_only,
             "search_error": search_error, "flat": flat, "flat_items": [],
             "pagination": None, "has_more_local": False},
        )

    return templates.TemplateResponse(
        request, "shop/catalog.html",
        {"title": "المتجر", "error": None,
         "local_items": local_items, "moved_items": moved_items,
         "supplier_groups": supplier_groups, "suppliers": suppliers,
         "categories": categories, "q": q, "supplier_filter": supplier_id,
         "category_filter": category_filter, "sort": sort, "section": section,
         "available_only": available_only, "search_error": search_error,
         "flat": flat, "flat_items": flat_items, "pagination": pagination,
         "has_more_local": has_more_local},
    )


async def shop_local_detail(request: Request):
    try:
        sub_id = int(request.path_params["sub_id"])
    except (KeyError, TypeError, ValueError):
        return _not_found(request, "المنتج غير موجود.")
    rates = await get_currency_rates()
    try:
        async with Database().session() as session:
            result = await session.execute(
                select(LocalSubscription)
                .options(selectinload(LocalSubscription.codes))
                .where(LocalSubscription.id == sub_id)
            )
            sub = result.scalar_one_or_none()
            if sub is None:
                return _not_found(request, "هذا الاشتراك غير موجود.")
            qty = int(getattr(sub, "sellable_quantity", None) or 0)
            title = sub.name
            is_active = bool(sub.is_active)
            description = (sub.description or "لا يوجد وصف.").strip()
            if len(description) > 500:
                description = description[:500] + "..."
            item = {
                "kind": "local", "id": sub.id, "name": sub.name,
                "category": sub.category or "عام", "description": description,
                "supplier_name": None, "image_url": None,
                "delivery_type": sub.delivery_type,
                "delivery": _DELIVERY_LABELS.get(
                    (sub.delivery_type or "codes").lower(), "أكواد"),
                "prices": _local_prices(sub, rates),
                "usd_value": sub.price,
                "stock": _local_stock(qty),
                "is_new": _is_new(getattr(sub, "created_at", None)),
                "last_synced": None,
                "fav_key": f"local:{sub.id}",
                "share_url": str(request.url),
            }
    except Exception:
        logger.exception("shop local detail failed")
        return _not_found(request, "تعذّر تحميل المنتج. حاول لاحقاً.")
    if not is_active:
        return _not_found(request, "هذا الاشتراك لم يعد متوفراً.")
    return templates.TemplateResponse(
        request, "shop/detail.html", {"title": title, "item": item, "error": None},
    )


async def shop_supplier_detail(request: Request):
    try:
        supplier_id = int(request.path_params["supplier_id"])
        product_id = int(request.path_params["product_id"])
    except (KeyError, TypeError, ValueError):
        return _not_found(request, "المنتج غير موجود.")
    rates = await get_currency_rates()
    try:
        async with Database().session() as session:
            prod = await session.get(SupplierProduct, product_id)
            # Same check as supplier_products.py:505.
            if prod is None or prod.supplier_id != supplier_id:
                return _not_found(request, "المنتج غير موجود.")
            sup = await session.get(Supplier, supplier_id)
            supplier_name = (sup.display_name or sup.name) if sup is not None else ""
            description = (prod.description or "").strip()
            if len(description) > 500:
                description = description[:500] + "..."
            is_visible = bool(prod.is_visible)
            item = _supplier_card(prod, rates, supplier_name)
            item["description"] = description or "لا يوجد وصف."
            item["share_url"] = str(request.url)
    except Exception:
        logger.exception("shop supplier detail failed")
        return _not_found(request, "تعذّر تحميل المنتج. حاول لاحقاً.")
    if not is_visible:
        # Hidden products are never linked from the catalog; a direct URL
        # must not open them either (unlike out-of-stock, which stays visible).
        return _not_found(request, "المنتج غير موجود.")
    return templates.TemplateResponse(
        request, "shop/detail.html",
        {"title": item["name"], "item": item, "error": None},
    )


async def _shop_slash_redirect(request: Request):
    qs = ("?" + request.url.query) if request.url.query else ""
    return RedirectResponse(url=f"/shop{qs}", status_code=301)


shop_routes = [
    Route("/shop", shop_catalog, methods=["GET", "HEAD"]),
    Route("/shop/", _shop_slash_redirect, methods=["GET", "HEAD"]),
    Route("/shop/product/local/{sub_id:int}", shop_local_detail,
          methods=["GET", "HEAD"]),
    Route("/shop/product/supplier/{supplier_id:int}/{product_id:int}",
          shop_supplier_detail, methods=["GET", "HEAD"]),
]