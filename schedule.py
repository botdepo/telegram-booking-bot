"""Расписание: рабочие дни, часы и свободные окна. Без Telegram — легко проверить и поменять."""
import os
import re
from datetime import date, datetime, timedelta, timezone

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# Часовой пояс записи. По умолчанию Москва (UTC+3); для Екатеринбурга задайте
# в переменных окружения TZ_OFFSET_HOURS=5 и т.п.
TZ = timezone(timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "3"))))


def now_local() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def fmt_minutes(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_hours(text: str) -> tuple[int, int] | None:
    """'10-19' или '9:30-18:00' → (начало, конец) в минутах от полуночи."""
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d\d))?\s*[-–—]\s*(\d{1,2})(?::(\d\d))?\s*", text)
    if not m:
        return None
    start = int(m[1]) * 60 + int(m[2] or 0)
    end = int(m[3]) * 60 + int(m[4] or 0)
    if not (0 <= start < end <= 24 * 60):
        return None
    return start, end


def parse_days(text: str) -> set[int] | None:
    """'пн-пт', 'пн,ср,пт' или 'пн-сб' → номера дней недели (0 = пн)."""
    days: set[int] = set()
    for part in re.split(r"[,\s]+", text.strip().lower()):
        if not part:
            continue
        ends = re.split(r"[-–—]", part)
        if not all(e in WEEKDAYS for e in ends) or len(ends) > 2:
            return None
        a, b = WEEKDAYS.index(ends[0]), WEEKDAYS.index(ends[-1])
        if a > b:
            return None
        days.update(range(a, b + 1))
    return days or None


def fmt_days(days: set[int]) -> str:
    return ", ".join(WEEKDAYS[d] for d in sorted(days))


def day_slots(day: date, start: int, end: int, step: int, after: datetime) -> list[str]:
    """Окна дня 'HH:MM', которые целиком помещаются в рабочие часы и ещё не начались."""
    slots = []
    m = start
    while m + step <= end:
        t = datetime.combine(day, datetime.min.time()) + timedelta(minutes=m)
        if t > after:
            slots.append(fmt_minutes(m))
        m += step
    return slots


def fmt_day(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"


def fmt_slot(slot: str) -> str:
    """'2026-10-12 14:00' → 'пн 12.10 в 14:00'."""
    d, t = slot.split(" ")
    return f"{fmt_day(date.fromisoformat(d))} в {t}"
