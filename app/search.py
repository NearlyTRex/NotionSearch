"""Meilisearch index management and querying.

The index is derived entirely from SQLite, so it is safe to delete and rebuild
at any time without touching Notion.
"""

import contextlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta

import meilisearch
from meilisearch.errors import MeilisearchError

log = logging.getLogger("notionsearch.search")

MEILI_URL = os.environ.get("MEILI_URL", "http://localhost:7700")
MEILI_KEY = os.environ.get("MEILI_MASTER_KEY", "")
# Overridable so tests never touch the real index.
INDEX_UID = os.environ.get("MEILI_INDEX", "notion")

# Highlight markers. Deliberately not HTML — see query() below.
HL_PRE = "[[hl]]"
HL_POST = "[[/hl]]"

# Title matches should outrank body matches, so order matters here.
SEARCHABLE = ["title", "breadcrumb", "property_text", "content"]
FILTERABLE = [
    "object", "parent_id", "parent_title", "facets", "archived",
    "last_edited_ts", "created_ts",
    # Filterable so deletions can target ids without the deprecated ids= call.
    "notion_id",
]
SORTABLE = ["last_edited_ts", "created_ts", "title"]

# Default rules plus a recency tiebreaker: when two pages match equally well,
# the more recently edited one is almost always the one she wants.
RANKING_RULES = [
    "words",
    "typo",
    "proximity",
    "attribute",
    "sort",
    "exactness",
    "last_edited_ts:desc",
]


def client() -> meilisearch.Client:
    return meilisearch.Client(MEILI_URL, MEILI_KEY or None)


def healthy() -> bool:
    try:
        client().health()
        return True
    except Exception:
        return False


def ensure_index() -> None:
    """Create the index and apply settings. Safe to call repeatedly."""
    c = client()
    try:
        c.create_index(INDEX_UID, {"primaryKey": "id"})
    except MeilisearchError as exc:
        # Already existing is the normal case on every boot after the first.
        if "index_already_exists" not in str(exc):
            raise

    c.index(INDEX_UID).update_settings(
        {
            "searchableAttributes": SEARCHABLE,
            "filterableAttributes": FILTERABLE,
            "sortableAttributes": SORTABLE,
            "rankingRules": RANKING_RULES,
            "typoTolerance": {
                "enabled": True,
                # Be forgiving early: 1 typo from 4 chars, 2 from 8.
                "minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
            },
            "pagination": {"maxTotalHits": 5000},
        }
    )


def to_document(row) -> dict:
    """Map a SQLite `pages` row to a Meilisearch document."""
    try:
        properties = json.loads(row["properties"]) if row["properties"] else {}
    except (json.JSONDecodeError, TypeError):
        properties = {}

    # Flatten properties into "Name:Value" strings so arbitrary user-defined
    # property names can all share one filterable attribute.
    facets: list[str] = []
    for name, value in properties.items():
        if isinstance(value, list):
            facets.extend(f"{name}:{v}" for v in value)
        elif isinstance(value, bool):
            facets.append(f"{name}:{'Yes' if value else 'No'}")
        elif value not in (None, ""):
            facets.append(f"{name}:{value}")

    return {
        "id": row["id"].replace("-", ""),
        "notion_id": row["id"],
        "object": row["object"],
        "title": row["title"] or "Untitled",
        "content": row["content"] or "",
        "property_text": row["property_text"] or "",
        "breadcrumb": row["breadcrumb"] or "",
        "url": row["url"] or "",
        "icon": row["icon"] or "",
        "parent_id": row["parent_id"] or "",
        # .keys() is required: sqlite3.Row.__contains__ tests values, not keys,
        # so `"parent_title" in row` is always False and would silently blank
        # the location facet.
        "parent_title": row["parent_title"] if "parent_title" in row.keys() else "",  # noqa: SIM118
        "facets": facets,
        "archived": bool(row["archived"]),
        "created_ts": _ts(row["created_time"]),
        "last_edited_ts": _ts(row["last_edited_time"]),
        "last_edited_time": row["last_edited_time"] or "",
    }


def _ts(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except (ValueError, AttributeError):
        return 0


def add_documents(docs: list[dict]) -> int | None:
    """Queue documents for indexing. Returns the task id to wait on, if any."""
    if not docs:
        return None
    task = client().index(INDEX_UID).add_documents(docs)
    return getattr(task, "task_uid", None)


def wait_for(task_uids: list[int], timeout_ms: int = 120_000) -> None:
    """Block until indexing tasks finish.

    Meilisearch indexes asynchronously, so without this a sync can report
    "done" a moment before the new pages are actually searchable.
    """
    c = client()
    for uid in task_uids:
        if uid is None:
            continue
        try:
            c.wait_for_task(uid, timeout_in_ms=timeout_ms)
        except Exception as exc:
            log.warning("waiting on index task %s failed: %s", uid, exc)


def delete_documents(notion_ids: list[str]) -> list[int]:
    """Remove documents by Notion id. Returns task ids to wait on."""
    if not notion_ids:
        return []

    index = client().index(INDEX_UID)
    tasks: list[int] = []
    # Chunked so the filter expression stays a sane length.
    for i in range(0, len(notion_ids), 200):
        chunk = notion_ids[i : i + 200]
        values = ", ".join(f'"{_quote(n)}"' for n in chunk)
        task = index.delete_documents(filter=f"notion_id IN [{values}]")
        uid = getattr(task, "task_uid", None)
        if uid is not None:
            tasks.append(uid)
    return tasks


def clear_index() -> None:
    with contextlib.suppress(MeilisearchError):
        client().index(INDEX_UID).delete()
    ensure_index()


def stats() -> dict:
    try:
        return client().index(INDEX_UID).get_stats().__dict__
    except Exception:
        return {}


# --- querying -------------------------------------------------------------

RANGE_DAYS = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}


def _quote(value: str) -> str:
    """Escape a value for a Meilisearch filter string literal."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def build_filter(
    parent_title: str | None = None,
    obj: str | None = None,
    facets: list[str] | None = None,
    edited_within: str | None = None,
    include_archived: bool = False,
) -> list:
    """Compose a Meilisearch filter expression.

    Inner lists are OR'd, outer entries are AND'd.
    """
    clauses: list = []

    if not include_archived:
        clauses.append("archived = false")
    if parent_title:
        # Filters on the human-readable title, which is what the facet UI emits.
        clauses.append(f'parent_title = "{_quote(parent_title)}"')
    if obj in ("page", "database"):
        clauses.append(f'object = "{_quote(obj)}"')
    if edited_within in RANGE_DAYS:
        cutoff = datetime.now(UTC) - timedelta(days=RANGE_DAYS[edited_within])
        clauses.append(f"last_edited_ts > {int(cutoff.timestamp())}")
    if facets:
        # Same property OR'd together, different properties AND'd — the
        # behaviour people expect from faceted filters.
        by_prop: dict[str, list[str]] = {}
        for f in facets:
            prop = f.split(":", 1)[0]
            by_prop.setdefault(prop, []).append(f)
        for group in by_prop.values():
            clauses.append([f'facets = "{_quote(g)}"' for g in group])

    return clauses


def query(
    q: str,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "relevance",
    **filter_kwargs,
) -> dict:
    """Run a search. Empty query returns recent pages, which makes the landing
    page useful before she types anything."""
    opts: dict = {
        "limit": limit,
        "offset": offset,
        "attributesToHighlight": ["title", "content", "property_text"],
        "attributesToCrop": ["content"],
        "cropLength": 40,
        "cropMarker": "…",
        # Sentinels, not real tags: page content is untrusted, so the client
        # HTML-escapes the whole string and only then swaps these for <mark>.
        "highlightPreTag": HL_PRE,
        "highlightPostTag": HL_POST,
        "facets": ["facets", "parent_title", "object"],
        "showMatchesPosition": False,
    }

    filters = build_filter(**filter_kwargs)
    if filters:
        opts["filter"] = filters

    if sort_by == "recent" or (not q.strip() and sort_by == "relevance"):
        opts["sort"] = ["last_edited_ts:desc"]
    elif sort_by == "oldest":
        opts["sort"] = ["last_edited_ts:asc"]
    elif sort_by == "title":
        opts["sort"] = ["title:asc"]

    return client().index(INDEX_UID).search(q, opts)
