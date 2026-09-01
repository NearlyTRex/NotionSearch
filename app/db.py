"""SQLite storage: source of truth for synced Notion content and app config.

The Meilisearch index is derived from this and can always be rebuilt, so this
file is the only thing worth backing up.
"""

import contextlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("NOTIONSEARCH_DATA", "./data"))
DB_PATH = DATA_DIR / "notionsearch.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id                TEXT PRIMARY KEY,
    object            TEXT NOT NULL,          -- 'page' | 'database'
    title             TEXT NOT NULL DEFAULT '',
    url               TEXT,
    icon              TEXT,
    parent_id         TEXT,
    parent_type       TEXT,
    breadcrumb        TEXT NOT NULL DEFAULT '',
    created_time      TEXT,
    last_edited_time  TEXT,
    archived          INTEGER NOT NULL DEFAULT 0,
    properties        TEXT NOT NULL DEFAULT '{}',   -- JSON: flattened property values
    property_text     TEXT NOT NULL DEFAULT '',     -- searchable blob of property values
    content           TEXT NOT NULL DEFAULT '',     -- plain text of all blocks
    content_fetched   INTEGER NOT NULL DEFAULT 0,   -- 0 until blocks pulled at least once
    seen_at           TEXT,                         -- last sync run that saw it in Notion
    indexed_at        TEXT                          -- last time pushed to Meilisearch
);

CREATE INDEX IF NOT EXISTS idx_pages_parent  ON pages(parent_id);
CREATE INDEX IF NOT EXISTS idx_pages_edited  ON pages(last_edited_time);
CREATE INDEX IF NOT EXISTS idx_pages_seen    ON pages(seen_at);

CREATE TABLE IF NOT EXISTS sync_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,        -- running | ok | error | cancelled
    mode          TEXT NOT NULL,        -- incremental | full
    phase         TEXT,                 -- human-readable current step
    total         INTEGER DEFAULT 0,
    processed     INTEGER DEFAULT 0,
    updated       INTEGER DEFAULT 0,
    removed       INTEGER DEFAULT 0,
    error         TEXT
);
"""

_local = threading.local()


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    """One connection per thread. FastAPI runs sync endpoints in a threadpool."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _connect()
    return conn


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    # The Notion token lives in here, so keep the file owner-only.
    # Some filesystems (and Windows shares) reject chmod; that is not fatal.
    with contextlib.suppress(OSError):
        os.chmod(DB_PATH, 0o600)


# --- config helpers -------------------------------------------------------

def get_setting(key: str, default=None):
    row = get_conn().execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_setting(key: str, value) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO config(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def delete_setting(key: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM config WHERE key = ?", (key,))
