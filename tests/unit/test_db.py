"""Tests for app/db.py — schema, settings, and connection handling."""

import sqlite3
import threading


def test_schema_creates_expected_tables(store):
    names = {
        r["name"] for r in store.get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"config", "pages", "sync_runs"} <= names


def test_init_is_idempotent(store):
    store.init_db()
    store.init_db()
    assert store.get_conn().execute("SELECT COUNT(*) c FROM pages").fetchone()["c"] == 0


def test_wal_mode_enabled(store):
    mode = store.get_conn().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_db_file_is_owner_only(store):
    # The Notion API key lives in here.
    assert (store.DB_PATH.stat().st_mode & 0o077) == 0


def test_setting_round_trip(store):
    store.set_setting("notion_token", "ntn_secret")
    assert store.get_setting("notion_token") == "ntn_secret"


def test_setting_preserves_types(store):
    for key, value in [("b", True), ("n", 42), ("l", ["a", "b"]),
                       ("d", {"x": 1}), ("none", None)]:
        store.set_setting(key, value)
        assert store.get_setting(key) == value


def test_missing_setting_returns_default(store):
    assert store.get_setting("nope") is None
    assert store.get_setting("nope", "fallback") == "fallback"


def test_setting_overwrites(store):
    store.set_setting("k", "first")
    store.set_setting("k", "second")
    assert store.get_setting("k") == "second"
    count = store.get_conn().execute(
        "SELECT COUNT(*) c FROM config WHERE key = 'k'").fetchone()["c"]
    assert count == 1


def test_delete_setting(store):
    store.set_setting("k", "v")
    store.delete_setting("k")
    assert store.get_setting("k") is None
    store.delete_setting("k")  # deleting twice must not raise


def test_corrupt_setting_value_returns_default(store):
    with store.tx() as conn:
        conn.execute("INSERT INTO config(key, value) VALUES('bad', 'not json{')")
    assert store.get_setting("bad", "safe") == "safe"


def test_tx_rolls_back_on_error(store):
    store.set_setting("keep", "yes")
    try:
        with store.tx() as conn:
            conn.execute("INSERT INTO config(key, value) VALUES('tmp', '1')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store.get_setting("tmp") is None
    assert store.get_setting("keep") == "yes"


def test_page_id_is_unique(store):
    with store.tx() as conn:
        conn.execute("INSERT INTO pages (id, object) VALUES ('dup', 'page')")
    try:
        with store.tx() as conn:
            conn.execute("INSERT INTO pages (id, object) VALUES ('dup', 'page')")
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised


def test_each_thread_gets_its_own_connection(store):
    seen = {}

    def worker():
        seen[threading.get_ident()] = store.get_conn()

    main_conn = store.get_conn()
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(seen) == 3
    assert all(conn is not main_conn for conn in seen.values())


def test_init_survives_a_filesystem_that_rejects_chmod(tmp_path, monkeypatch):
    """Some filesystems (and Windows shares) don't support chmod; that's not fatal."""
    import os as _os

    from app import db as dbmod

    monkeypatch.setattr(dbmod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "nochmod.db")
    monkeypatch.setattr(dbmod._local, "conn", None, raising=False)

    def refuse(*args, **kwargs):
        raise OSError("chmod not supported here")

    monkeypatch.setattr(_os, "chmod", refuse)
    dbmod.init_db()  # must not raise

    assert dbmod.get_conn().execute("SELECT COUNT(*) c FROM pages").fetchone()["c"] == 0
    dbmod._local.conn.close()
    dbmod._local.conn = None
