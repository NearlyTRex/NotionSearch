# HTTP API

Served from `http://localhost:8080`. The web UI is built on exactly these
endpoints, so anything it does, you can script.

When `APP_PASSWORD` is set, everything except `/health` and `/api/status` needs a
session cookie from `POST /api/auth/login`.

## Search

### `GET /api/search`

| Parameter | Default | Meaning |
|---|---|---|
| `q` | `""` | Query text. Empty lists recent pages |
| `limit` | `20` | Results per page, capped at 100 |
| `offset` | `0` | For paging |
| `sort` | `relevance` | `relevance`, `recent`, `oldest`, `title` |
| `edited` | — | `day`, `week`, `month`, `quarter`, `year` |
| `object` | — | `page` or `database` |
| `parent` | — | Parent page title |
| `facet` | — | `"Property:Value"`. Repeatable |
| `include_archived` | `false` | Include archived pages |

Repeated `facet` values for the same property are OR'd; different properties are
AND'd.

```bash
curl -G http://localhost:8080/api/search \
  --data-urlencode "q=lisbon" \
  --data-urlencode "facet=Status:Planning" \
  --data-urlencode "facet=Tags:Travel" \
  --data-urlencode "edited=month"
```

```json
{
  "query": "lisbon",
  "hits": [
    {
      "id": "22222222222222222222222222222222",
      "notion_id": "22222222-2222-2222-2222-222222222222",
      "title": "Lisbon Trip 2026",
      "breadcrumb": "Travel",
      "url": "https://notion.so/...",
      "icon": "✈️",
      "facets": ["Status:Planning", "Tags:Travel"],
      "last_edited_time": "2026-03-01T12:00:00.000Z",
      "_formatted": { "title": "[[hl]]Lisbon[[/hl]] Trip 2026", "content": "…" }
    }
  ],
  "total": 1,
  "processing_ms": 2,
  "facets": { "facets": { "Status:Planning": 2 } },
  "limit": 20,
  "offset": 0
}
```

`_formatted` marks matches with `[[hl]]` / `[[/hl]]` sentinels rather than HTML
tags. Escape the text before replacing them — see
[Architecture](architecture.md#highlighting).

### `GET /api/page/{id}`

Full stored record, including complete text. Accepts the Notion id with or without
dashes. `404` if not synced.

## Status

### `GET /api/status`

Whether it's configured, signed in, and synced, plus page counts and current sync
state. Never returns the API key.

### `GET /health`

`{"ok": true, "search": true}`. Unauthenticated — used by the container
healthcheck.

## Sync

### `POST /api/sync`

```json
{"mode": "incremental"}
```

`incremental` re-reads only changed pages; `full` re-reads everything. Returns
`409` if a sync is already running.

### `GET /api/sync/status`

```json
{
  "running": true,
  "status": "running",
  "phase": "reading 42 new or changed pages",
  "total": 42, "processed": 17,
  "updated": 17, "removed": 0,
  "percent": 40.5,
  "error": null
}
```

Poll this while `running` is true.

### `POST /api/sync/cancel`

Stops the current sync at the next safe point. Work already done is kept.

### `GET /api/sync/history?limit=10`

Past runs, newest first.

## Configuration

### `POST /api/config/notion`

```json
{"token": "ntn_..."}
```

Validated against Notion before being stored. `400` with a readable message if
rejected.

### `DELETE /api/config/notion`

Removes the key, all synced pages, and the index. Notion is untouched.

## Auth

`POST /api/auth/login` with `{"password": "..."}` sets an HttpOnly session cookie
lasting 30 days. `POST /api/auth/logout` clears it. Both are no-ops when
`APP_PASSWORD` is unset.

## Errors

Non-2xx responses are `{"error": "..."}` with a message meant to be shown to a
person.

| Code | Meaning |
|---|---|
| `400` | No API key configured, or Notion rejected it |
| `401` | Not signed in |
| `404` | Page not found locally |
| `409` | A sync is already running |
| `422` | Malformed request body |
| `503` | Search engine unreachable |
