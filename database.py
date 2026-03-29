import aiosqlite
import os
import re
from datetime import datetime, timezone

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "notes.db"))

CATEGORIES = {"работа", "личное", "идея"}
CATEGORY_ALIASES = {
    "work": "работа", "job": "работа",
    "personal": "личное", "life": "личное",
    "idea": "идея", "ideas": "идея", "идеи": "идея",
}


def extract_category(text: str) -> tuple[str | None, str]:
    """Extract #category from text. Returns (category, cleaned_text)."""
    match = re.search(r'#(\S+)', text)
    if not match:
        return None, text
    tag = match.group(1).lower()
    category = CATEGORY_ALIASES.get(tag, tag)
    if category in CATEGORIES:
        cleaned = text[:match.start()] + text[match.end():]
        return category, cleaned.strip()
    return None, text


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                category TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Add category column if upgrading from old schema
        try:
            await db.execute("ALTER TABLE notes ADD COLUMN category TEXT")
        except Exception:
            pass  # column already exists
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                auto_daily_enabled INTEGER NOT NULL DEFAULT 1
            )
        """)
        # Add columns if upgrading from old schema
        for col in ("timezone TEXT", "last_auto_daily TEXT", "auto_daily_hour INTEGER DEFAULT 22"):
            try:
                await db.execute(f"ALTER TABLE user_settings ADD COLUMN {col}")
            except Exception:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                is_recurring INTEGER NOT NULL DEFAULT 0,
                repeat_days_left INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        await db.commit()


async def save_note(user_id: int, text: str) -> tuple[int, str | None]:
    category, cleaned_text = extract_category(text)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO notes (user_id, text, category, created_at) VALUES (?, ?, ?, ?)",
            (user_id, cleaned_text, category, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cursor.lastrowid, category


async def get_notes(user_id: int, since: datetime | None = None, category: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        conditions = ["user_id = ?"]
        params: list = [user_id]
        if since:
            conditions.append("created_at >= ?")
            params.append(since.isoformat())
        if category:
            conditions.append("category = ?")
            params.append(category)
        where = " AND ".join(conditions)
        cursor = await db.execute(
            f"SELECT id, text, category, created_at FROM notes WHERE {where} ORDER BY created_at ASC",
            params,
        )
        rows = await cursor.fetchall()
        return [{"id": row["id"], "text": row["text"], "category": row["category"], "created_at": row["created_at"]} for row in rows]


async def get_notes_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM notes WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0]


async def delete_note(note_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_recent_notes(user_id: int, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, text, category, created_at FROM notes WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def clear_notes(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM notes WHERE user_id = ?", (user_id,)
        )
        await db.commit()
        return cursor.rowcount


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT user_id FROM notes")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def set_auto_daily(user_id: int, enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (user_id, auto_daily_enabled) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET auto_daily_enabled = ?",
            (user_id, int(enabled), int(enabled)),
        )
        await db.commit()


async def get_auto_daily(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT auto_daily_enabled FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] == 1 if row else True  # enabled by default


async def set_timezone(user_id: int, tz: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (user_id, timezone) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET timezone = ?",
            (user_id, tz, tz),
        )
        await db.commit()


async def get_timezone(user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT timezone FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_last_auto_daily(user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT last_auto_daily FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_auto_daily_hour(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT auto_daily_hour FROM user_settings WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 22


async def set_auto_daily_hour(user_id: int, hour: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (user_id, auto_daily_hour) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET auto_daily_hour = ?",
            (user_id, hour, hour),
        )
        await db.commit()


async def set_last_auto_daily(user_id: int, date_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_settings (user_id, last_auto_daily) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_auto_daily = ?",
            (user_id, date_str, date_str),
        )
        await db.commit()


async def save_reminder(user_id: int, text: str, remind_at: str,
                        is_recurring: bool = False, repeat_days_left: int | None = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, text, remind_at, is_recurring, repeat_days_left, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, text, remind_at, int(is_recurring), repeat_days_left,
             datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return cursor.lastrowid


async def get_pending_reminders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, user_id, text, remind_at, is_recurring, repeat_days_left "
            "FROM reminders ORDER BY remind_at ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_user_reminders(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, text, remind_at, is_recurring, repeat_days_left "
            "FROM reminders WHERE user_id = ? ORDER BY remind_at ASC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def delete_reminder(reminder_id: int, user_id: int | None = None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        if user_id is not None:
            cursor = await db.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id)
            )
        else:
            cursor = await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await db.commit()
        return cursor.rowcount > 0


async def update_reminder_next(reminder_id: int, remind_at: str, repeat_days_left: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE reminders SET remind_at = ?, repeat_days_left = ? WHERE id = ?",
            (remind_at, repeat_days_left, reminder_id),
        )
        await db.commit()
