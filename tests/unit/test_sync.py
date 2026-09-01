"""Tests for app/sync.py — the Notion → SQLite → Meilisearch pipeline."""

import asyncio

import pytest
from conftest import NOW, meili_required, rt, seed_pages

from app import sync

# --- a stand-in for the Notion API ---------------------------------------

class FakeNotion:
    """Mimics the slice of NotionClient that sync uses."""

    def __init__(self, items=None, blocks=None):
        self.items = items or []
        self.blocks = blocks or {}
        self.block_calls = []
        self.closed = False

    async def search_all(self):
        for item in self.items:
            yield item

    async def block_children(self, block_id):
        self.block_calls.append(block_id)
        for block in self.blocks.get(block_id, []):
            yield block

    async def close(self):
        self.closed = True


def page(pid, title, *, parent=None, edited="2026-01-01T00:00:00.000Z",
         archived=False, props=None):
    """A Notion search result shaped like the real API returns."""
    properties = {"Name": {"type": "title", "title": rt(title)}}
    properties.update(props or {})
    return {
        "object": "page", "id": pid, "url": f"https://notion.so/{pid}",
        "created_time": "2025-01-01T00:00:00.000Z", "last_edited_time": edited,
        "archived": archived, "properties": properties,
        "parent": ({"type": "page_id", "page_id": parent} if parent
                   else {"type": "workspace", "workspace": True}),
    }


def para(text, *, bid="b1", children=False):
    return {"object": "block", "id": bid, "type": "paragraph",
            "has_children": children, "paragraph": {"rich_text": rt(text)}}


@pytest.fixture
def stub_index(monkeypatch):
    """Replace Meilisearch calls so pipeline logic can be tested without it."""
    sent = {"docs": [], "deleted": [], "cleared": 0}
    monkeypatch.setattr(sync.search, "ensure_index", lambda: None)
    monkeypatch.setattr(sync.search, "clear_index",
                        lambda: sent.__setitem__("cleared", sent["cleared"] + 1))
    monkeypatch.setattr(sync.search, "add_documents",
                        lambda docs: sent["docs"].extend(docs))
    monkeypatch.setattr(sync.search, "delete_documents",
                        lambda ids: sent["deleted"].extend(ids) or [])
    monkeypatch.setattr(sync.search, "wait_for", lambda tasks, **kw: None)
    return sent


@pytest.fixture(autouse=True)
def fresh_state():
    sync.STATE = sync.SyncState()
    yield


# --- metadata upsert ------------------------------------------------------

def test_new_page_needs_content(store):
    pid, needs = sync._upsert_meta(page("p1", "Lisbon"), "t0")
    assert pid == "p1" and needs is True

    row = store.get_conn().execute("SELECT * FROM pages WHERE id='p1'").fetchone()
    assert row["title"] == "Lisbon" and row["object"] == "page"


def test_unchanged_page_skips_content_refetch(store):
    item = page("p1", "Lisbon", edited="2026-01-01T00:00:00.000Z")
    sync._upsert_meta(item, "t0")
    sync._save_content("p1", "body")

    _, needs = sync._upsert_meta(item, "t1")
    assert needs is False, "an unchanged page must not be re-read from Notion"


def test_edited_page_is_refetched(store):
    sync._upsert_meta(page("p1", "Lisbon", edited="2026-01-01T00:00:00.000Z"), "t0")
    sync._save_content("p1", "body")

    _, needs = sync._upsert_meta(page("p1", "Lisbon", edited="2026-06-01T00:00:00.000Z"), "t1")
    assert needs is True


def test_page_seen_before_content_fetched_is_retried(store):
    """Metadata stored but content fetch failed: try again next sync."""
    sync._upsert_meta(page("p1", "X"), "t0")
    _, needs = sync._upsert_meta(page("p1", "X"), "t1")
    assert needs is True


def test_title_update_is_persisted(store):
    sync._upsert_meta(page("p1", "Old"), "t0")
    sync._upsert_meta(page("p1", "New"), "t1")
    row = store.get_conn().execute("SELECT title FROM pages WHERE id='p1'").fetchone()
    assert row["title"] == "New"


@pytest.mark.parametrize("parent,expected_type,expected_id", [
    ({"type": "workspace", "workspace": True}, "workspace", ""),
    ({"type": "page_id", "page_id": "parent-1"}, "page_id", "parent-1"),
    ({"type": "database_id", "database_id": "db-1"}, "database_id", "db-1"),
])
def test_parent_shapes(store, parent, expected_type, expected_id):
    item = page("p1", "X")
    item["parent"] = parent
    sync._upsert_meta(item, "t0")

    row = store.get_conn().execute("SELECT * FROM pages WHERE id='p1'").fetchone()
    assert row["parent_type"] == expected_type
    assert row["parent_id"] == expected_id


def test_archived_and_trashed_flags(store):
    sync._upsert_meta(page("p1", "Gone", archived=True), "t0")
    item = page("p2", "Trashed")
    item["in_trash"] = True
    sync._upsert_meta(item, "t0")

    rows = dict(store.get_conn().execute("SELECT id, archived FROM pages").fetchall())
    assert rows["p1"] == 1 and rows["p2"] == 1


def test_properties_are_flattened_on_upsert(store):
    item = page("p1", "Trip", props={
        "Status": {"type": "status", "status": {"name": "Planning"}}})
    sync._upsert_meta(item, "t0")

    row = store.get_conn().execute("SELECT properties FROM pages WHERE id='p1'").fetchone()
    assert '"Status": "Planning"' in row["properties"]


# --- block collection -----------------------------------------------------

async def test_collect_blocks_flattens_nested_children():
    client = FakeNotion(blocks={
        "page-1": [para("Top", bid="b1", children=True)],
        "b1": [para("Nested", bid="b2")],
    })
    assert await sync._collect_blocks(client, "page-1") == ["Top", "Nested"]


async def test_collect_blocks_does_not_descend_into_child_pages():
    """A child page is indexed on its own; recursing would duplicate it."""
    client = FakeNotion(blocks={
        "page-1": [{"id": "cp", "type": "child_page", "has_children": True,
                    "child_page": {"title": "Sub"}}],
        "cp": [para("should not appear", bid="x")],
    })
    lines = await sync._collect_blocks(client, "page-1")
    assert lines == ["Sub"] and "cp" not in client.block_calls


async def test_collect_blocks_stops_at_depth_limit(monkeypatch):
    monkeypatch.setattr(sync, "MAX_BLOCK_DEPTH", 3)
    # A block that is forever its own child would otherwise recurse for ever.
    client = FakeNotion(blocks={"loop": [para("deep", bid="loop", children=True)]})
    lines = await sync._collect_blocks(client, "loop")
    assert len(lines) == 3


async def test_collect_blocks_skips_empty_text():
    client = FakeNotion(blocks={"p": [
        para("kept", bid="b1"), {"id": "d", "type": "divider", "divider": {}}]})
    assert await sync._collect_blocks(client, "p") == ["kept"]


# --- breadcrumbs ----------------------------------------------------------

def test_breadcrumbs_follow_ancestry(store):
    for pid, title, parent in [("a", "Travel", None), ("b", "2026", "a"), ("c", "Lisbon", "b")]:
        sync._upsert_meta(page(pid, title, parent=parent), "t0")
    sync._build_breadcrumbs()

    crumbs = dict(store.get_conn().execute("SELECT id, breadcrumb FROM pages").fetchall())
    assert crumbs["a"] == ""
    assert crumbs["b"] == "Travel"
    assert crumbs["c"] == "Travel / 2026"


def test_breadcrumbs_survive_a_parent_cycle(store):
    """Corrupt data must not hang the sync."""
    sync._upsert_meta(page("a", "A", parent="b"), "t0")
    sync._upsert_meta(page("b", "B", parent="a"), "t0")
    sync._build_breadcrumbs()  # must terminate

    crumbs = dict(store.get_conn().execute("SELECT id, breadcrumb FROM pages").fetchall())
    assert len(crumbs["a"].split(" / ")) <= 10


def test_breadcrumb_ignores_unknown_parent(store):
    sync._upsert_meta(page("a", "Orphan", parent="missing"), "t0")
    sync._build_breadcrumbs()
    row = store.get_conn().execute("SELECT breadcrumb FROM pages WHERE id='a'").fetchone()
    assert row["breadcrumb"] == ""


# --- indexing and pruning -------------------------------------------------

def test_reindex_sends_only_changed_pages(store, stub_index):
    seed_pages(store)
    assert sync._reindex(only_changed=True) == 6
    stub_index["docs"].clear()

    # Nothing touched since: a second pass should send nothing.
    assert sync._reindex(only_changed=True) == 0
    assert stub_index["docs"] == []


def test_reindex_full_resends_everything(store, stub_index):
    seed_pages(store)
    sync._reindex(only_changed=True)
    assert sync._reindex(only_changed=False) == 6


def test_reindex_excludes_archived(store, stub_index):
    seed_pages(store)
    with store.tx() as conn:
        conn.execute("UPDATE pages SET archived = 1 WHERE title = 'Travel'")
    assert sync._reindex(only_changed=False) == 5


def test_prune_removes_pages_notion_no_longer_returns(store, stub_index):
    seed_pages(store)
    # seen_at values are always ISO timestamps, compared lexicographically.
    with store.tx() as conn:
        conn.execute("UPDATE pages SET seen_at = ? WHERE title = 'Travel'",
                     ("2020-01-01T00:00:00+00:00",))

    removed = sync._prune(NOW.isoformat())
    assert removed == 1
    assert stub_index["deleted"] == ["11111111-1111-1111-1111-111111111111"]

    remaining = store.get_conn().execute("SELECT COUNT(*) c FROM pages").fetchone()["c"]
    assert remaining == 5


def test_prune_keeps_everything_when_all_seen(store, stub_index):
    seed_pages(store)
    assert sync._prune(NOW.isoformat()) == 0


def test_prune_removes_rows_never_seen(store, stub_index):
    """seen_at NULL means the row predates any completed sync."""
    seed_pages(store)
    with store.tx() as conn:
        conn.execute("UPDATE pages SET seen_at = NULL WHERE title = 'Travel'")
    assert sync._prune(NOW.isoformat()) == 1


# --- full pipeline --------------------------------------------------------

async def test_run_sync_end_to_end(store, stub_index, monkeypatch):
    fake = FakeNotion(
        items=[page("p1", "Lisbon"), page("p2", "Budget", parent="p1")],
        blocks={"p1": [para("Flights booked")], "p2": [para("1200 euros")]},
    )
    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: fake)

    await sync.run_sync("ntn_test")

    assert sync.STATE.status == "ok"
    assert sync.STATE.updated == 2
    assert fake.closed, "the HTTP client must be closed"

    rows = dict(store.get_conn().execute("SELECT id, content FROM pages").fetchall())
    assert rows["p1"] == "Flights booked"
    assert store.get_setting("last_sync_ok") is True
    assert store.get_setting("last_sync_at")


async def test_run_sync_second_pass_is_incremental(store, stub_index, monkeypatch):
    fake = FakeNotion(items=[page("p1", "Lisbon")], blocks={"p1": [para("body")]})
    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: fake)

    await sync.run_sync("ntn_test")
    fake.block_calls.clear()
    await sync.run_sync("ntn_test")

    assert fake.block_calls == [], "unchanged pages must not be re-read"
    assert sync.STATE.updated == 0


async def test_run_sync_records_history(store, stub_index, monkeypatch):
    monkeypatch.setattr(sync, "NotionClient",
                        lambda *a, **k: FakeNotion(items=[page("p1", "X")], blocks={"p1": []}))
    await sync.run_sync("ntn_test")

    run = store.get_conn().execute("SELECT * FROM sync_runs ORDER BY id DESC").fetchone()
    assert run["status"] == "ok" and run["finished_at"]


async def test_run_sync_reports_notion_errors(store, stub_index, monkeypatch):
    from app.notion import NotionError

    class Failing(FakeNotion):
        async def search_all(self):
            raise NotionError("API token is invalid.", 401, "unauthorized")
            yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: Failing())
    await sync.run_sync("bad_token")

    assert sync.STATE.status == "error"
    assert "invalid" in sync.STATE.error
    assert store.get_setting("last_sync_ok") is False


async def test_page_content_failure_keeps_metadata(store, stub_index, monkeypatch):
    """One unreadable page must not abort the whole sync."""
    from app.notion import NotionError

    class Partial(FakeNotion):
        async def block_children(self, block_id):
            if block_id == "p2":
                raise NotionError("Not found", 404)
            for b in self.blocks.get(block_id, []):
                yield b

    fake = Partial(items=[page("p1", "Good"), page("p2", "Bad")],
                   blocks={"p1": [para("kept")]})
    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: fake)

    await sync.run_sync("ntn_test")

    assert sync.STATE.status == "ok"
    titles = {r["title"] for r in store.get_conn().execute("SELECT title FROM pages")}
    assert titles == {"Good", "Bad"}


async def test_cancel_stops_the_run(store, stub_index, monkeypatch):
    class Slow(FakeNotion):
        async def search_all(self):
            for item in self.items:
                sync.STATE.cancel = True  # cancel arrives mid-discovery
                yield item

    monkeypatch.setattr(sync, "NotionClient",
                        lambda *a, **k: Slow(items=[page("p1", "A"), page("p2", "B")]))
    await sync.run_sync("ntn_test")
    assert sync.STATE.status == "cancelled"


async def test_start_refuses_concurrent_runs(store, stub_index, monkeypatch):
    monkeypatch.setattr(sync, "NotionClient",
                        lambda *a, **k: FakeNotion(items=[], blocks={}))
    sync.STATE.running = True
    assert await sync.start("ntn_test") is False


def test_progress_percent_is_bounded():
    state = sync.SyncState()
    state.total, state.processed = 10, 25
    assert state.snapshot()["percent"] == 100.0

    state.total = 0
    assert state.snapshot()["percent"] == 0.0


@meili_required
async def test_run_sync_makes_pages_searchable(store, index, monkeypatch):
    """The real thing: after a sync, the content is actually findable."""
    fake = FakeNotion(
        items=[page("p1", "Lisbon Trip")],
        blocks={"p1": [para("Staying in Alfama near the tram")]},
    )
    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: fake)

    await sync.run_sync("ntn_test")

    hits = index.query("alfama")["hits"]
    assert [h["title"] for h in hits] == ["Lisbon Trip"]


# --- remaining branches ---------------------------------------------------

def test_persist_without_a_run_is_a_no_op(store):
    """Called before a run row exists; must not blow up."""
    sync.STATE.run_id = None
    sync._persist(phase="nowhere")  # must not raise


def test_non_string_parent_id_is_ignored(store):
    """Notion returns parent.workspace as a boolean, not an id."""
    item = page("p1", "X")
    item["parent"] = {"type": "database_id", "database_id": {"unexpected": "shape"}}
    sync._upsert_meta(item, "t0")

    row = store.get_conn().execute("SELECT parent_id FROM pages WHERE id='p1'").fetchone()
    assert row["parent_id"] == ""


async def test_full_mode_clears_local_content_and_index(store, stub_index, monkeypatch):
    fake = FakeNotion(items=[page("p1", "Lisbon")], blocks={"p1": [para("body")]})
    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: fake)

    await sync.run_sync("ntn_test")          # populate
    fake.block_calls.clear()
    await sync.run_sync("ntn_test", mode="full")

    assert stub_index["cleared"] >= 1, "a full rebuild must drop the index"
    assert fake.block_calls == ["p1"], "a full rebuild must re-read every page"
    assert sync.STATE.updated == 1


async def test_progress_is_persisted_for_large_workspaces(store, stub_index, monkeypatch):
    """The >25 and >10 checkpoints only fire on bigger runs."""
    items = [page(f"p{i}", f"Page {i}") for i in range(30)]
    blocks = {f"p{i}": [para(f"body {i}")] for i in range(30)}
    monkeypatch.setattr(sync, "NotionClient",
                        lambda *a, **k: FakeNotion(items=items, blocks=blocks))

    await sync.run_sync("ntn_test")

    assert sync.STATE.status == "ok"
    assert sync.STATE.updated == 30
    run = store.get_conn().execute("SELECT * FROM sync_runs ORDER BY id DESC").fetchone()
    assert run["processed"] == 30


async def test_cancel_during_content_fetch(store, stub_index, monkeypatch):
    class CancelOnFetch(FakeNotion):
        async def block_children(self, block_id):
            sync.STATE.cancel = True
            for b in self.blocks.get(block_id, []):
                yield b

    fake = CancelOnFetch(items=[page("p1", "A"), page("p2", "B")],
                         blocks={"p1": [para("x")], "p2": [para("y")]})
    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: fake)

    await sync.run_sync("ntn_test")
    assert sync.STATE.status == "cancelled"


async def test_unexpected_error_is_surfaced_not_swallowed(store, stub_index, monkeypatch):
    class Exploding(FakeNotion):
        async def search_all(self):
            raise RuntimeError("disk on fire")
            yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(sync, "NotionClient", lambda *a, **k: Exploding())
    await sync.run_sync("ntn_test")

    assert sync.STATE.status == "error"
    assert "disk on fire" in sync.STATE.error
    assert store.get_setting("last_sync_ok") is False


async def test_start_launches_a_run(store, stub_index, monkeypatch):
    monkeypatch.setattr(sync, "NotionClient",
                        lambda *a, **k: FakeNotion(items=[page("p1", "A")], blocks={"p1": []}))
    assert await sync.start("ntn_test") is True

    for _ in range(100):
        if not sync.STATE.running:
            break
        await asyncio.sleep(0.05)
    assert sync.STATE.status == "ok"


def test_request_cancel_only_applies_while_running():
    sync.STATE.running = False
    assert sync.request_cancel() is False

    sync.STATE.running = True
    assert sync.request_cancel() is True
    assert sync.STATE.cancel is True
