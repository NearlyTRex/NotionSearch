"""Sync engine: pull Notion into SQLite, then push SQLite into Meilisearch.

Only one sync runs at a time. Progress is kept both in memory (for polling)
and in the sync_runs table (so history survives a restart).
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from . import db, extract, search
from .notion import NotionClient, NotionError

log = logging.getLogger("notionsearch.sync")

# Overlap network latency while the client throttle still enforces Notion's
# rate limit; this roughly halves wall-clock time on a large workspace.
CONCURRENCY = 5
MAX_BLOCK_DEPTH = 6
INDEX_BATCH = 200

# Recursing into these would duplicate content that is indexed as its own page.
NO_RECURSE = {"child_page", "child_database"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SyncState:
    """In-memory view of the current or most recent run."""

    def __init__(self):
        self.running = False
        self.run_id: int | None = None
        self.phase = "idle"
        self.total = 0
        self.processed = 0
        self.updated = 0
        self.removed = 0
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.status = "idle"
        self.error: str | None = None
        self.cancel = False

    def snapshot(self) -> dict:
        pct = 0.0
        if self.total:
            pct = round(min(self.processed / self.total, 1.0) * 100, 1)
        return {
            "running": self.running,
            "status": self.status,
            "phase": self.phase,
            "total": self.total,
            "processed": self.processed,
            "updated": self.updated,
            "removed": self.removed,
            "percent": pct,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


STATE = SyncState()
_lock = asyncio.Lock()
# Holds a reference to the running sync task: without one it can be garbage
# collected mid-flight, which cancels the sync for no visible reason.
_task: asyncio.Task | None = None


def _persist(**fields):
    if STATE.run_id is None:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.tx() as conn:
        conn.execute(
            f"UPDATE sync_runs SET {cols} WHERE id = ?",
            (*fields.values(), STATE.run_id),
        )


def _set_phase(phase: str):
    STATE.phase = phase
    log.info("sync: %s", phase)
    _persist(phase=phase)


# --- Notion -> SQLite -----------------------------------------------------

async def _collect_blocks(client: NotionClient, block_id: str, depth: int = 0) -> list[str]:
    """Recursively gather the plain text of every block under a page."""
    if depth >= MAX_BLOCK_DEPTH:
        return []

    lines: list[str] = []
    children_to_walk: list[str] = []

    async for block in client.block_children(block_id):
        text = extract.block_text(block)
        if text:
            lines.append(text)
        if block.get("has_children") and block.get("type") not in NO_RECURSE:
            children_to_walk.append(block["id"])

    for child_id in children_to_walk:
        lines.extend(await _collect_blocks(client, child_id, depth + 1))

    return lines


def _upsert_meta(item: dict, seen_at: str) -> tuple[str, bool]:
    """Insert or update page metadata. Returns (page_id, needs_content_fetch)."""
    page_id = item["id"]
    obj = item.get("object", "page")
    title = extract.title_of(item)
    parent = item.get("parent") or {}
    parent_type = parent.get("type", "")
    parent_id = parent.get(parent_type, "") if parent_type != "workspace" else ""
    if not isinstance(parent_id, str):
        parent_id = ""

    last_edited = item.get("last_edited_time", "")
    archived = 1 if (item.get("archived") or item.get("in_trash")) else 0
    properties, property_text = extract.flatten_properties(item.get("properties") or {})

    existing = db.get_conn().execute(
        "SELECT last_edited_time, content_fetched FROM pages WHERE id = ?", (page_id,)
    ).fetchone()

    needs_content = (
        existing is None
        or not existing["content_fetched"]
        or existing["last_edited_time"] != last_edited
    )

    with db.tx() as conn:
        conn.execute(
            """
            INSERT INTO pages (id, object, title, url, icon, parent_id, parent_type,
                               created_time, last_edited_time, archived,
                               properties, property_text, seen_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                object=excluded.object, title=excluded.title, url=excluded.url,
                icon=excluded.icon, parent_id=excluded.parent_id,
                parent_type=excluded.parent_type,
                created_time=excluded.created_time,
                last_edited_time=excluded.last_edited_time,
                archived=excluded.archived, properties=excluded.properties,
                property_text=excluded.property_text, seen_at=excluded.seen_at
            """,
            (
                page_id, obj, title, item.get("url", ""), extract.icon_of(item),
                parent_id, parent_type, item.get("created_time", ""), last_edited,
                archived, json.dumps(properties), property_text, seen_at,
            ),
        )

    return page_id, needs_content


def _save_content(page_id: str, content: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE pages SET content = ?, content_fetched = 1 WHERE id = ?",
            (content, page_id),
        )


def _build_breadcrumbs() -> None:
    """Resolve each page's ancestor titles into a searchable breadcrumb string."""
    conn = db.get_conn()
    titles = {
        row["id"]: (row["title"], row["parent_id"])
        for row in conn.execute("SELECT id, title, parent_id FROM pages")
    }

    updates = []
    for page_id, (_, parent_id) in titles.items():
        crumbs: list[str] = []
        cursor, guard = parent_id, 0
        while cursor and cursor in titles and guard < 10:
            crumbs.append(titles[cursor][0])
            cursor = titles[cursor][1]
            guard += 1
        updates.append((" / ".join(reversed(crumbs)), page_id))

    with db.tx() as conn2:
        conn2.executemany("UPDATE pages SET breadcrumb = ? WHERE id = ?", updates)


# --- SQLite -> Meilisearch ------------------------------------------------

def _reindex(only_changed: bool = True) -> int:
    """Push pages to Meilisearch. Returns the number of documents sent."""
    search.ensure_index()
    conn = db.get_conn()

    sql = """
        SELECT p.*, COALESCE(parent.title, '') AS parent_title
        FROM pages p
        LEFT JOIN pages parent ON parent.id = p.parent_id
        WHERE p.archived = 0
    """
    if only_changed:
        sql += " AND (p.indexed_at IS NULL OR p.indexed_at < p.seen_at)"

    rows = conn.execute(sql).fetchall()
    sent = 0
    tasks: list[int] = []
    for i in range(0, len(rows), INDEX_BATCH):
        batch = rows[i : i + INDEX_BATCH]
        task = search.add_documents([search.to_document(r) for r in batch])
        if task is not None:
            tasks.append(task)
        sent += len(batch)

    # Don't report "done" until the new pages are actually searchable.
    search.wait_for(tasks)

    if rows:
        stamp = _now()
        with db.tx() as c:
            c.executemany(
                "UPDATE pages SET indexed_at = ? WHERE id = ?",
                [(stamp, r["id"]) for r in rows],
            )
    return sent


def _prune(seen_at: str) -> int:
    """Remove pages Notion no longer returns (deleted or unshared)."""
    conn = db.get_conn()
    stale = conn.execute(
        "SELECT id FROM pages WHERE seen_at IS NULL OR seen_at < ?", (seen_at,)
    ).fetchall()
    if not stale:
        return 0

    ids = [r["id"] for r in stale]
    search.wait_for(search.delete_documents(ids))
    with db.tx() as c:
        c.executemany("DELETE FROM pages WHERE id = ?", [(i,) for i in ids])
    return len(ids)


# --- orchestration --------------------------------------------------------

async def run_sync(token: str, mode: str = "incremental") -> None:
    """Full sync pipeline. Assumes the caller holds the run slot."""
    seen_at = _now()
    STATE.running = True
    STATE.status = "running"
    STATE.cancel = False
    STATE.error = None
    STATE.started_at = seen_at
    STATE.finished_at = None
    STATE.total = STATE.processed = STATE.updated = STATE.removed = 0

    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO sync_runs (started_at, status, mode, phase) VALUES (?,?,?,?)",
            (seen_at, "running", mode, "starting"),
        )
        STATE.run_id = cur.lastrowid

    client = NotionClient(token)
    try:
        if mode == "full":
            _set_phase("clearing local copy")
            with db.tx() as conn:
                conn.execute("UPDATE pages SET content_fetched = 0, indexed_at = NULL")
            search.clear_index()

        # Phase 1 — discover everything the integration can see.
        _set_phase("discovering pages")
        needs_content: list[str] = []
        async for item in client.search_all():
            if STATE.cancel:
                raise asyncio.CancelledError()
            page_id, changed = _upsert_meta(item, seen_at)
            STATE.total += 1
            if changed:
                needs_content.append(page_id)
            if STATE.total % 25 == 0:
                _persist(total=STATE.total, phase=f"discovering pages ({STATE.total} found)")

        _persist(total=STATE.total)
        log.info("discovered %d objects, %d need content", STATE.total, len(needs_content))

        # Phase 2 — pull block content for new/changed pages only.
        _set_phase(f"reading {len(needs_content)} new or changed pages")
        STATE.total = max(len(needs_content), 1)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def fetch_one(pid: str):
            async with semaphore:
                if STATE.cancel:
                    return
                try:
                    lines = await _collect_blocks(client, pid)
                    _save_content(pid, "\n".join(lines))
                    STATE.updated += 1
                except NotionError as exc:
                    # Keep the metadata we already have; skip the body.
                    log.warning("content fetch failed for %s: %s", pid, exc.message)
                finally:
                    STATE.processed += 1
                    if STATE.processed % 10 == 0:
                        _persist(processed=STATE.processed, updated=STATE.updated)

        if needs_content:
            await asyncio.gather(*(fetch_one(p) for p in needs_content))

        if STATE.cancel:
            raise asyncio.CancelledError()

        # Phase 3 — ancestor titles, so "notes in Travel" style queries work.
        _set_phase("building page paths")
        await asyncio.to_thread(_build_breadcrumbs)

        # Phase 4 — push to the search index.
        _set_phase("updating search index")
        sent = await asyncio.to_thread(_reindex, mode != "full")
        log.info("indexed %d documents", sent)

        # Phase 5 — drop anything Notion stopped returning.
        _set_phase("removing deleted pages")
        STATE.removed = await asyncio.to_thread(_prune, seen_at)

        STATE.status = "ok"
        _set_phase("done")
        db.set_setting("last_sync_at", _now())
        db.set_setting("last_sync_ok", True)

    except asyncio.CancelledError:
        STATE.status = "cancelled"
        STATE.phase = "cancelled"
        log.info("sync cancelled")
    except NotionError as exc:
        STATE.status = "error"
        STATE.error = exc.message
        STATE.phase = "failed"
        log.error("sync failed: %s", exc.message)
        db.set_setting("last_sync_ok", False)
    except Exception as exc:
        STATE.status = "error"
        STATE.error = str(exc)
        STATE.phase = "failed"
        log.exception("sync crashed")
        db.set_setting("last_sync_ok", False)
    finally:
        await client.close()
        STATE.running = False
        STATE.finished_at = _now()
        _persist(
            finished_at=STATE.finished_at, status=STATE.status, phase=STATE.phase,
            total=STATE.total, processed=STATE.processed, updated=STATE.updated,
            removed=STATE.removed, error=STATE.error,
        )


async def start(token: str, mode: str = "incremental") -> bool:
    """Kick off a sync unless one is already running."""
    async with _lock:
        if STATE.running:
            return False
        global _task
        _task = asyncio.create_task(run_sync(token, mode))
        # Let the task set running=True before the caller polls status.
        await asyncio.sleep(0.05)
        return True


def request_cancel() -> bool:
    if not STATE.running:
        return False
    STATE.cancel = True
    return True
