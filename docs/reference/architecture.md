# Architecture

```
Notion API  ──sync──>  SQLite (source of truth)  ──index──>  Meilisearch
                            │                                     │
                            └──────────>  FastAPI  <──────────────┘
                                             │
                                        Web UI (localhost:8080)
```

Two containers: `api` (the app) and `meilisearch` (the search engine). Only `api`
is published to the host, and only on `127.0.0.1`.

## Why SQLite and Meilisearch

SQLite holds every synced page and is the only thing worth backing up. The
Meilisearch index is derived from it and completely disposable.

That split means a rebuild never has to re-download from Notion — which matters,
because Notion is the slow, rate-limited part. Losing or corrupting the index costs
a rebuild, not a re-sync.

## The sync pipeline

`app/sync.py`, in five phases:

1. **Discover** — walk Notion's search endpoint for every page and database the
   integration can see, storing metadata. Compare each page's `last_edited_time`
   against what's stored to decide whether its content needs re-reading.
2. **Read content** — fetch blocks only for new and changed pages, recursively,
   flattening them to plain text.
3. **Build paths** — resolve each page's ancestor titles into a breadcrumb.
4. **Index** — push changed pages to Meilisearch and wait for indexing to finish,
   so "sync complete" means genuinely searchable.
5. **Prune** — delete anything Notion stopped returning, covering both deleted and
   unshared pages.

Only one sync runs at a time. Progress lives in memory for polling and in the
`sync_runs` table for history.

### Rate limiting

Notion allows about three requests per second. `app/notion.py` throttles every call
and retries on 429 with the server's `Retry-After`, on 5xx, and on network errors.
A 4xx is raised immediately since it won't fix itself.

Requests run five at a time. The throttle still enforces the overall rate, but
overlapping the waiting roughly halves wall-clock time on a large workspace.

### Robustness

A single unreadable page — deleted mid-sync, or permission-denied — is logged and
skipped rather than failing the run. Block recursion is depth-limited so a
pathological structure can't loop forever, and breadcrumb building tolerates
parent cycles.

## Search behaviour

Configured in `app/search.py`:

- **Searchable fields, in priority order**: title, breadcrumb, property text, then
  body content. Meilisearch's `attribute` ranking rule means a title match outranks
  a body match.
- **Recency tiebreak**: `last_edited_ts:desc` is appended to the ranking rules, so
  equally-good matches are ordered newest-first.
- **Typo tolerance**: one typo from four characters, two from eight.
- **Facets**: Notion's arbitrary user-defined property names are flattened into
  `"Name:Value"` strings under a single filterable attribute, which avoids needing
  to know the schema in advance.

### Highlighting

Meilisearch wraps matches in `[[hl]]` sentinels rather than real `<mark>` tags. Page
content is untrusted — it can contain anything someone typed into Notion — so the
browser escapes the entire string first and only then swaps the sentinels for
`<mark>`. Emitting HTML directly would be an injection route.

## Data model

`pages` holds one row per Notion page or database: identity, title, parent,
timestamps, flattened properties, and extracted text. `config` holds settings and
the API key. `sync_runs` holds history.

The database file is `0600` and owned by you, because the Notion key is in it.

## Layout

| Path | What's in it |
|---|---|
| `app/notion.py` | Notion API client — throttling, retries, pagination |
| `app/extract.py` | Blocks and properties → plain text |
| `app/sync.py` | The sync pipeline |
| `app/search.py` | Index configuration and queries |
| `app/db.py` | SQLite schema and settings |
| `app/main.py` | REST API and static serving |
| `web/` | Frontend — no build step, no framework |
| `tests/unit/` | Per-module tests mirroring `app/` |
| `tests/integration/` | The real stack, over HTTP |
| `pyproject.toml` | Project metadata, test config, dev extras |
| `docker/` | `Dockerfile`, `docker-compose.yml`, `.env.example` |
