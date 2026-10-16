"""Formatters موحّدة لتواريخ ووقت SQLAdmin (بتوقيت اليمن)."""

import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# توقيت اليمن (UTC+3)
YEMEN_TZ = ZoneInfo("Asia/Aden")

# أيام الأسبوع والشهور بالعربي
WEEKDAYS_AR = {
    0: "الإثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس",
    4: "الجمعة", 5: "السبت", 6: "الأحد",
}
MONTHS_AR = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
    7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}


def _to_yemen(value: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """يحوّل التاريخ إلى توقيت اليمن."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(YEMEN_TZ)


def _time_12h(dt: datetime.datetime) -> str:
    """يرجع الوقت بصيغة 12 ساعة مع ص/م."""
    h = dt.hour
    period = "ص" if h < 12 else "م"
    h12 = h % 12 or 12
    return f"{h12:02d}:{dt.minute:02d}:{dt.second:02d} {period}"


def _date_ar(dt: datetime.datetime) -> str:
    """يرجع التاريخ بالعربي."""
    return f"{dt.day} {MONTHS_AR[dt.month]} {dt.year}"


def fmt_dt_smart(value: Optional[datetime.datetime]) -> str:
    """
    صيغة ذكية:
      - اليوم: الوقت فقط
      - أمس: أمس، الوقت
      - أقدم: التاريخ + الوقت
    """
    dt = _to_yemen(value)
    if dt is None:
        return ""

    now = datetime.datetime.now(YEMEN_TZ)
    today = now.date()
    target = dt.date()

    if target == today:
        return _time_12h(dt)
    if target == today - datetime.timedelta(days=1):
        return f"أمس، {_time_12h(dt)}"
    if target.year == now.year:
        return f"{dt.day} {MONTHS_AR[dt.month]}، {_time_12h(dt)}"
    return f"{_date_ar(dt)}، {_time_12h(dt)}"


def fmt_dt_full(value: Optional[datetime.datetime]) -> str:
    """صيغة كاملة: التاريخ + الوقت + يوم الأسبوع."""
    dt = _to_yemen(value)
    if dt is None:
        return ""
    return f"{WEEKDAYS_AR[dt.weekday()]}، {_date_ar(dt)}، {_time_12h(dt)}"
