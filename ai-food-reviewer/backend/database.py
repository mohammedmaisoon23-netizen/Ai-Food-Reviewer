"""Tiny SQLite layer (stdlib only). Swap for SQLAlchemy/PostgreSQL if you outgrow it."""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Optional

from config import DB_PATH

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id           TEXT PRIMARY KEY,
    shop_input   TEXT NOT NULL,
    shop_name    TEXT,
    shop_area    TEXT,
    dishes       TEXT,
    language     TEXT NOT NULL,
    character    TEXT NOT NULL,
    status       TEXT NOT NULL,      -- queued | running | done | failed
    stage        TEXT,
    progress     INTEGER DEFAULT 0,
    script_json  TEXT,
    audio_path   TEXT,
    video_path   TEXT,
    error        TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
"""


@contextmanager
def _conn():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)
        # Anything left "running" from a previous process can never finish.
        c.execute(
            "UPDATE reviews SET status='failed', error='Server restarted during generation', updated_at=? "
            "WHERE status IN ('queued','running')",
            (time.time(),),
        )


def create_review(shop_input: str, dishes: str, language: str, character: str) -> str:
    rid = uuid.uuid4().hex[:12]
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO reviews (id, shop_input, dishes, language, character, status, stage, progress, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, shop_input, dishes, language, character, "queued", "Queued", 0, now, now),
        )
    return rid


def update_review(rid: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE reviews SET {cols} WHERE id=?", (*fields.values(), rid))


def get_review(rid: str) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone()
    return _row_to_dict(row) if row else None


def list_reviews(limit: int = 20) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM reviews ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("script_json"):
        try:
            d["script"] = json.loads(d["script_json"])
        except json.JSONDecodeError:
            d["script"] = None
    else:
        d["script"] = None
    d.pop("script_json", None)
    return d
