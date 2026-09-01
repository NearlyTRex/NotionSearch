# Development setup

Running outside Docker gives you auto-reload.

## Dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

That installs the runtime dependencies plus the test and lint tools in one go.
Python 3.12 or newer.

Runtime dependencies are pinned in `requirements.txt`, which `pyproject.toml` reads
as its single source of truth — so there is only ever one list to edit. The `[dev]`
extra (pytest, pytest-asyncio, pytest-cov, ruff) lives in `pyproject.toml`.

For a runtime-only install, `pip install -r requirements.txt` still works and is
what the container does.

## Meilisearch

The app needs one running. Easiest is Docker, publishing the port so your local
app can reach it:

```bash
docker run -d --rm -p 127.0.0.1:7700:7700 \
  -e MEILI_MASTER_KEY=dev_master_key_at_least_16_bytes \
  -e MEILI_NO_ANALYTICS=true \
  --name meili-dev getmeili/meilisearch:v1.11
```

> The production `docker/docker-compose.yml` deliberately does **not** publish this
> port — only the app container needs it. So `docker compose up -d meilisearch` will
> not work for local development; use the command above.

Or install the binary:

```bash
curl -L https://install.meilisearch.com | sh
./meilisearch --master-key=dev_master_key_at_least_16_bytes
```

The key must be at least 16 bytes.

## The app

```bash
MEILI_MASTER_KEY=dev_master_key_at_least_16_bytes \
  .venv/bin/uvicorn app.main:app --reload --port 8080
```

`--reload` restarts on file changes. The frontend has no build step, so editing
anything in `web/` just needs a browser refresh.

Data goes to `./data/` by default; set `NOTIONSEARCH_DATA` to put it elsewhere.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `NOTIONSEARCH_DATA` | `./data` | Where SQLite lives |
| `MEILI_URL` | `http://localhost:7700` | Search engine address |
| `MEILI_MASTER_KEY` | *(empty)* | Must match the engine's key |
| `MEILI_INDEX` | `notion` | Index name |
| `NOTION_API_BASE` | `https://api.notion.com/v1` | Notion API root |
| `APP_PASSWORD` | *(empty)* | Blank disables the login |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose logs |

`MEILI_INDEX` and `NOTION_API_BASE` exist so tests can run against throwaway
targets. Don't point them anywhere real.

## Working on it without a Notion key

The integration tests run the whole stack against a stand-in Notion server, so you
can exercise sync and search without touching a real workspace. See
[Testing](testing.md).

## Building the container

```bash
cd docker
docker compose build
docker compose up -d
```

After changing `requirements.txt`, rebuild — dependencies are a cached layer.

## Code layout

See [Architecture](../reference/architecture.md).
