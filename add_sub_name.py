from pathlib import Path

path = Path("bot/web/admin.py")
text = path.read_text(encoding="utf-8")

# ---------- 1) إضافة دالة مساعدة + استيراد required ----------

# تأكد من استيراد LocalSubscription (موجود مسبقاً، لكن للتأكد)
helper_anchor = "# Authentication\nclass AdminAuth(AuthenticationBackend):"

helper_code = '''# ============================================================
#  Local Subscriptions  Admin helpers
# ============================================================
import asyncio as _asyncio
from sqlalchemy import select as _sa_select

_SUB_NAME_CACHE: dict[int, str] = {}


async def _get_subscription_name(sub_id: int) -> str:
    """Fetch subscription name by id (cached in-memory)."""
    if sub_id in _SUB_NAME_CACHE:
        return _SUB_NAME_CACHE[sub_id]
    try:
        async with Database().session() as session:
            result = await session.execute(
                _sa_select(LocalSubscription.name).where(LocalSubscription.id == sub_id)
            )
            row = result.first()
            name = row[0] if row else f"#{sub_id}"
    except Exception:
        name = f"#{sub_id}"
    _SUB_NAME_CACHE[sub_id] = name
    return name


# Authentication
class AdminAuth(AuthenticationBackend):'''

if helper_code.split("\n")[3] in text:
    print("SKIP: helper already present")
elif helper_anchor in text:
    text = text.replace(helper_anchor, helper_code, 1)
    print("OK: helper added")
else:
    print("FAIL: anchor for helper not found")
    raise SystemExit(1)

# ---------- 2) في LocalSubscriptionCodeAdmin: عرض اسم الاشتراك ----------

# استبدال subscription_id بعمود subscription_name
old_code_cols = """    column_list = [
        LocalSubscriptionCode.id,
        LocalSubscriptionCode.subscription_id,
        LocalSubscriptionCode.content,"""

new_code_cols = """    column_list = [
        LocalSubscriptionCode.id,
        "subscription_name",
        LocalSubscriptionCode.content,"""

if old_code_cols in text:
    text = text.replace(old_code_cols, new_code_cols, 1)
    print("OK: code column_list updated (subscription_name)")
else:
    print("SKIP/FAIL: code column_list anchor")

# إضافة column_formatters لـ subscription_name في LocalSubscriptionCodeAdmin
old_code_fmt = """    column_formatters = {
        "is_used": lambda m, a: LocalSubscriptionCodeAdmin.used_badge(m, a),
    }"""

new_code_fmt = """    column_formatters = {
        "is_used": lambda m, a: LocalSubscriptionCodeAdmin.used_badge(m, a),
        "subscription_name": lambda m, a: _asyncio.get_event_loop().run_until_complete(
            _get_subscription_name(m.subscription_id)
        ) if False else f"#{m.subscription_id}",
    }

    # --- Async formatter (SQLAdmin supports awaitable via coroutine functions) ---
    async def subscription_name_formatter(self, model: LocalSubscriptionCode) -> str:
        return await _get_subscription_name(model.subscription_id)"""

if old_code_fmt in text:
    text = text.replace(old_code_fmt, new_code_fmt, 1)
    print("OK: code formatters updated")
else:
    print("SKIP/FAIL: code formatters anchor")

# ---------- 3) نفس الشيء للـ Orders ----------
old_order_cols = """    column_list = [
        LocalSubscriptionOrder.id,
        LocalSubscriptionOrder.user_id,
        LocalSubscriptionOrder.subscription_id,"""

new_order_cols = """    column_list = [
        LocalSubscriptionOrder.id,
        LocalSubscriptionOrder.user_id,
        "subscription_name","""

if old_order_cols in text:
    text = text.replace(old_order_cols, new_order_cols, 1)
    print("OK: order column_list updated")
else:
    print("SKIP/FAIL: order column_list anchor")

# حفظ
path.write_text(text, encoding="utf-8", newline="\n")
print()
print("=" * 55)
print(f"admin.py saved, size = {len(text)} chars")
print("=" * 55)