import json

en_path = "bot/i18n/languages/en.json"
ar_path = "bot/i18n/languages/ar.json"

with open(en_path, encoding="utf-8") as f:
    data = json.load(f)

translations = {
    "btn.shop": "🏪 المتجر",
    "btn.search": "🔍 البحث عن المنتجات",
    "btn.rules": "📜 القوانين",
    "btn.profile": "👤 الملف الشخصي",
    "btn.support": "🆘 الدعم",
    "btn.back": "⬅️ رجوع",
    "btn.close": "✖️ إغلاق",
    "btn.buy": "🛒 شراء",
    "btn.yes": "✅ نعم",
    "btn.no": "❌ لا",
    "btn.pay": "💳 دفع",

    "admin.goods.add.prompt.price": "💰 أدخل سعر المنتج:",
    "admin.goods.add.price.invalid": "❌ السعر غير صحيح",
    "admin.goods.add.prompt.category": "📂 أدخل تصنيف المنتج:",
    "admin.goods.add.category.not_found": "❌ التصنيف غير موجود",
    "admin.goods.add.infinity.question": "هل المنتج غير محدود؟",

    "errors.something_wrong": "❌ حدث خطأ، حاول مرة أخرى.",
    "errors.invalid_data": "❌ بيانات غير صحيحة"
}

data.update(translations)

with open(ar_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Arabic language created:", len(data), "keys")