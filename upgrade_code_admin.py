from pathlib import Path
import re

path = Path("bot/web/admin.py")
text = path.read_text(encoding="utf-8")

# إذا كان محدّثاً مسبقاً
if "class LocalSubscriptionCodeAdmin" not in text:
    print("FAIL: LocalSubscriptionCodeAdmin not found")
    raise SystemExit(1)

# استبدال الكتلة بالكامل حتى class BotButtonView
pattern = re.compile(
    r'(class LocalSubscriptionCodeAdmin\(ModelView, model=LocalSubscriptionCode\):.*?)(?=\nclass BotButtonView)',
    re.DOTALL,
)
m = pattern.search(text)
if not m:
    print("FAIL: could not locate CodeAdmin block")
    raise SystemExit(1)

old_block = m.group(1)
print(f"Old block: {len(old_block)} chars")

new_block = '''class LocalSubscriptionCodeAdmin(ModelView, model=LocalSubscriptionCode):
    """Admin view for redeemable codes/keys. Filter by subscription for clarity."""
    name = "أكواد الاشتراكات"
    name_plural = "أكواد الاشتراكات المحلية"
    icon = "fa-solid fa-key"

    can_delete = True
    can_create = True
    can_edit = True

    column_list = [
        LocalSubscriptionCode.id,
        "subscription_name",
        LocalSubscriptionCode.content,
        "used_badge_col",
        "used_by_username",
        LocalSubscriptionCode.order_id,
        LocalSubscriptionCode.used_at,
        LocalSubscriptionCode.created_at,
    ]
    column_searchable_list = [
        LocalSubscriptionCode.content,
        LocalSubscriptionCode.used_by,
    ]
    column_sortable_list = [
        LocalSubscriptionCode.id,
        LocalSubscriptionCode.is_used,
        LocalSubscriptionCode.created_at,
    ]
    column_default_sort = [("created_at", True)]
    column_filters = [
        LocalSubscriptionCode.subscription_id,
        LocalSubscriptionCode.is_used,
    ]
    page_size = 50

    column_labels = {
        "subscription_name": "الاشتراك",
        "content": "الكود / المحتوى",
        "used_badge_col": "الحالة",
        "used_by_username": "استخدمه",
        "order_id": "الطلب",
        "used_at": "تاريخ الاستخدام",
        "created_at": "تاريخ الإضافة",
    }

    form_columns = [
        LocalSubscriptionCode.subscription_id,
        LocalSubscriptionCode.content,
        LocalSubscriptionCode.is_used,
        LocalSubscriptionCode.used_by,
        LocalSubscriptionCode.order_id,
    ]

    def subscription_name_label(model: LocalSubscriptionCode, _attr) -> str:
        sub = getattr(model, "subscription", None)
        if sub is not None:
            return str(sub.name or f"#{model.subscription_id}")
        return f"#{model.subscription_id}"

    def used_badge(model: LocalSubscriptionCode, _attr) -> Markup:
        if model.is_used:
            return Markup('<span style="background:#d9534f;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;"> مستخدم</span>')
        return Markup('<span style="background:#5cb85c;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;"> متاح</span>')

    def used_by_username_label(model: LocalSubscriptionCode, _attr) -> str:
        if not model.used_by:
            return ""
        return f"@{model.used_by}"

    def list_query(self, request):
        return super().list_query(request).options(joinedload(LocalSubscriptionCode.subscription))

    column_formatters = {
        "subscription_name": lambda m, a: LocalSubscriptionCodeAdmin.subscription_name_label(m, a),
        "used_badge_col": lambda m, a: LocalSubscriptionCodeAdmin.used_badge(m, a),
        "used_by_username": lambda m, a: LocalSubscriptionCodeAdmin.used_by_username_label(m, a),
    }


'''

text = text.replace(old_block, new_block, 1)
path.write_text(text, encoding="utf-8", newline="\n")
print("OK: LocalSubscriptionCodeAdmin updated")
print(f"   New size: {len(text)} chars")