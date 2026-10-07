"""Хранилище в SQLite: настройки расписания и записи клиентов.

На botdepo писать на диск можно только в /data — эта папка переживает
перезапуски и передеплои. Остальная файловая система только для чтения.
Локально (где /data нет) база ляжет в ./data рядом с кодом.
"""
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR") or ("/data" if Path("/data").is_dir() else "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

_db = sqlite3.connect(DATA_DIR / "booking.db")
_db.executescript(
    """
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    -- slot: местное время 'YYYY-MM-DD HH:MM'; status: new → confirmed, или cancelled
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        slot TEXT NOT NULL,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new',
        reminded INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    -- одно время — одна живая запись, даже если двое нажали одновременно
    CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot ON bookings (slot) WHERE status != 'cancelled';
    """
)
_db.commit()

Booking = tuple[int, int, str, str, str, str]  # id, user_id, slot, name, phone, status


def get(key: str, default: str | None = None) -> str | None:
    row = _db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def put(key: str, value: str) -> None:
    _db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    _db.commit()


def claim_owner(user_id: int) -> bool:
    """Первый, кто написал /start, становится владельцем. True — если это он."""
    if get("owner_id") is None:
        put("owner_id", str(user_id))
    return get("owner_id") == str(user_id)


def owner_id() -> int | None:
    value = get("owner_id")
    return int(value) if value else None


def book(user_id: int, slot: str, name: str, phone: str) -> int | None:
    """id новой записи или None, если время уже заняли."""
    try:
        cur = _db.execute(
            "INSERT INTO bookings (user_id, slot, name, phone) VALUES (?, ?, ?, ?)", (user_id, slot, name, phone)
        )
    except sqlite3.IntegrityError:
        return None
    _db.commit()
    return cur.lastrowid


def taken_slots(day: str) -> set[str]:
    rows = _db.execute(
        "SELECT slot FROM bookings WHERE slot LIKE ? AND status != 'cancelled'", (day + " %",)
    ).fetchall()
    return {r[0] for r in rows}


def get_booking(booking_id: int) -> Booking | None:
    return _db.execute(
        "SELECT id, user_id, slot, name, phone, status FROM bookings WHERE id = ?", (booking_id,)
    ).fetchone()


def set_status(booking_id: int, status: str) -> None:
    _db.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    _db.commit()


def upcoming(now: str, user_id: int | None = None, until: str | None = None) -> list[Booking]:
    """Живые записи начиная с now (и до until), по времени."""
    sql = "SELECT id, user_id, slot, name, phone, status FROM bookings WHERE status != 'cancelled' AND slot >= ?"
    args: list = [now]
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    if until is not None:
        sql += " AND slot < ?"
        args.append(until)
    return _db.execute(sql + " ORDER BY slot", args).fetchall()


def due_reminders(now: str, until: str) -> list[Booking]:
    """Подтверждённые записи в окне [now, until), о которых ещё не напомнили."""
    return _db.execute(
        "SELECT id, user_id, slot, name, phone, status FROM bookings "
        "WHERE status = 'confirmed' AND reminded = 0 AND slot >= ? AND slot < ?",
        (now, until),
    ).fetchall()


def mark_reminded(booking_id: int) -> None:
    _db.execute("UPDATE bookings SET reminded = 1 WHERE id = ?", (booking_id,))
    _db.commit()
