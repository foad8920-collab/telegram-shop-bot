from pathlib import Path

# ============================================================
#  1) models/main.py  إضافة relationship
# ============================================================
mp = Path("bot/database/models/main.py")
mt = mp.read_text(encoding="utf-8")

# في LocalSubscriptionCode
old_rel = """    used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def __str__(self):
        preview = (self.content or "")[:30]
        state = "USED" if self.is_used else "FREE"
        return f"[{state}] {preview}"
"""

new_rel = """    used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    subscription: Mapped["LocalSubscription"] = relationship(
        "LocalSubscription", lazy="joined", foreign_keys=[subscription_id]
    )

    def __str__(self):
        preview = (self.content or "")[:30]
        state = "USED" if self.is_used else "FREE"
        return f"[{state}] {preview}"
"""

if "subscription: Mapped" in mt and "foreign_keys=[subscription_id]" in mt:
    print("SKIP: relationship already exists in models")
elif old_rel in mt:
    mt = mt.replace(old_rel, new_rel, 1)
    mp.write_text(mt, encoding="utf-8", newline="\n")
    print("OK: relationship added to LocalSubscriptionCode")
else:
    print("FAIL: model anchor not found")
    raise SystemExit(1)

# ============================================================
#  2) admin.py  استبدال subscription_id بالعلاقة
# ============================================================
ap = Path("bot/web/admin.py")
at = ap.read_text(encoding="utf-8")

# --- LocalSubscriptionCodeAdmin ---
old1 = """    column_list = [
        LocalSubscriptionCode.id,
        LocalSubscriptionCode.subscription_id,
        LocalSubscriptionCode.content,"""
new1 = """    column_list = [
        LocalSubscriptionCode.id,
        LocalSubscriptionCode.subscription,
        LocalSubscriptionCode.content,"""

if old1 in at:
    at = at.replace(old1, new1, 1)
    print("OK: code column_list -> relationship")
else:
    print("SKIP: code column_list anchor (already changed?)")

# --- LocalSubscriptionOrderAdmin ---
old2 = """    column_list = [
        LocalSubscriptionOrder.id,
        LocalSubscriptionOrder.user_id,
        LocalSubscriptionOrder.subscription_id,"""
new2 = """    column_list = [
        LocalSubscriptionOrder.id,
        LocalSubscriptionOrder.user_id,
        LocalSubscriptionOrder.subscription,"""

if old2 in at:
    at = at.replace(old2, new2, 1)
    print("OK: order column_list -> relationship")
else:
    print("SKIP: order column_list anchor (already changed?)")

ap.write_text(at, encoding="utf-8", newline="\n")
print()
print("=" * 55)
print("DONE")
print("=" * 55)