"""Shared fixtures for both test tiers.

tests/unit/       mirrors the app package: test_<module>.py covers app/<module>.py
tests/integration/ drives the stack as shipped, over HTTP

Meilisearch-backed tests are skipped automatically when no server is running,
so the suite stays useful without one. When a server is available they run
against a throwaway index, never the real `notion` one.
"""

import contextlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Project root, so tests can import `app`; and this directory, so tests in the
# unit/ and integration/ subdirectories can import these shared helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db, search

TEST_INDEX = "notion_pytest"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh SQLite database isolated to this test."""
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    # get_conn caches per thread; clear it so the new path takes effect.
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    db.init_db()
    yield db
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
        db._local.conn = None


def _meili_up() -> bool:
    try:
        search.client().health()
        return True
    except Exception:
        return False


MEILI_UP = _meili_up()

meili_required = pytest.mark.skipif(
    not MEILI_UP,
    reason="Meilisearch not running (set MEILI_URL / MEILI_MASTER_KEY to enable)",
)


def pytest_configure(config):
    """Fail fast in CI rather than silently skipping the search tests."""
    if os.environ.get("REQUIRE_MEILI") == "1" and not MEILI_UP:
        raise pytest.UsageError(
            "REQUIRE_MEILI=1 but Meilisearch is unreachable at "
            f"{search.MEILI_URL}. Start it, or unset REQUIRE_MEILI."
        )


def pytest_terminal_summary(terminalreporter):
    """Search is the point of this project; say plainly when it went untested."""
    if not MEILI_UP:
        terminalreporter.write_sep(
            "!", "Meilisearch was NOT running: search behaviour was not tested",
            yellow=True,
        )


@pytest.fixture
def index(monkeypatch):
    """A disposable Meilisearch index, torn down afterwards."""
    monkeypatch.setattr(search, "INDEX_UID", TEST_INDEX)
    search.clear_index()
    yield search
    with contextlib.suppress(Exception):
        search.client().index(TEST_INDEX).delete()


# --- sample data ---------------------------------------------------------

NOW = datetime.now(UTC)


def days_ago(n: int) -> str:
    return (NOW - timedelta(days=n)).isoformat()


def rt(text: str) -> list[dict]:
    """A rich_text array shaped like Notion's."""
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


# (id, object, title, parent, icon, content, days_old, property_text, properties)
SAMPLE_PAGES = [
    ("11111111-1111-1111-1111-111111111111", "page", "Travel", None, "", "", 400, "", {}),
    ("22222222-2222-2222-2222-222222222222", "page", "Lisbon Trip 2026",
     "11111111-1111-1111-1111-111111111111", "✈️",
     "Flights booked for March. Staying in Alfama near the tram.\n"
     "Budget is 1200 euros total.", 2, "Remember a plug adapter.",
     {"Status": "Planning", "Tags": ["Travel", "2026"]}),
    ("33333333-3333-3333-3333-333333333333", "page", "Quarterly Budget", None, "💰",
     "Q1 spending review. Rent, groceries, subscriptions.", 10, "",
     {"Status": "Done", "Tags": ["Finance"]}),
    ("44444444-4444-4444-4444-444444444444", "database", "Reading List", None, "📚",
     "Books to read this year.", 45, "", {}),
    ("55555555-5555-5555-5555-555555555555", "page", "Café Résumé Notes",
     "11111111-1111-1111-1111-111111111111", "",
     "Notes written at the café about the résumé rewrite.", 200, "",
     {"Status": "Archived", "Tags": ["Career"]}),
    ("66666666-6666-6666-6666-666666666666", "page", "Untitled meeting", None, "",
     "", 1, "Attendee: Sam Rivera. Topic: renewal pricing.",
     {"Status": "Planning", "Tags": ["Work"]}),
]


def seed_pages(database, pages=SAMPLE_PAGES) -> None:
    """Insert sample rows straight into the pages table."""
    with database.tx() as conn:
        conn.execute("DELETE FROM pages")
        for pid, obj, title, parent, icon, content, age, ptext, props in pages:
            conn.execute(
                """INSERT INTO pages (id, object, title, url, icon, parent_id,
                       parent_type, created_time, last_edited_time, archived,
                       properties, property_text, content, content_fetched, seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,1,?)""",
                (pid, obj, title, f"https://notion.so/{pid.replace('-', '')}", icon,
                 parent, "page_id" if parent else "workspace",
                 days_ago(age + 30), days_ago(age), json.dumps(props),
                 ptext, content, NOW.isoformat()),
            )
