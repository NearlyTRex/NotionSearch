# Testing

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

## Two tiers

| Directory | What it is |
|---|---|
| `tests/unit/` | Mirrors the app package — `test_notion.py` covers `app/notion.py`, and so on. Fast, no servers |
| `tests/integration/` | The stack as shipped: real uvicorn, real Meilisearch, a stand-in Notion server, driven over HTTP |

Integration tests are marked, so you can pick a tier:

```bash
pytest tests/unit -q              # by directory
pytest -m "not integration" -q    # by marker
pytest -m integration -q          # integration only
```

### What each tier is for

Unit tests use a mock HTTP transport for Notion and an isolated SQLite file. They
run in under a second and cover the fiddly logic: block extraction, retry and
rate-limit behaviour, filter construction, incremental sync decisions.

Integration tests patch nothing. They start the app with the same command the
Dockerfile uses, point it at a stand-in Notion, and walk the real journey — paste
key, sync, poll, search. This is the tier that catches a broken start command, a
bad static mount, or a pipeline that only works in-process.

## Meilisearch

Search tests need it. Start one with a published port:

```bash
docker run -d --rm -p 127.0.0.1:7700:7700 \
  -e MEILI_MASTER_KEY=testkey1234567890 \
  -e MEILI_NO_ANALYTICS=true \
  --name meili-test getmeili/meilisearch:v1.11

MEILI_MASTER_KEY=testkey1234567890 .venv/bin/python -m pytest -q
```

Without it, those tests **skip** and the run prints:

```
!!!!! Meilisearch was NOT running: search behaviour was not tested !!!!!
```

That banner matters. Search is the point of the project, and a green run that
skipped it is not a passing build.

### In CI

Set `REQUIRE_MEILI=1` to turn skipping into a hard error:

```bash
MEILI_MASTER_KEY=testkey1234567890 REQUIRE_MEILI=1 .venv/bin/python -m pytest -q
```

## Isolation

Tests never touch real data:

- Each test gets a throwaway SQLite file under pytest's `tmp_path`
- Search tests use a `notion_pytest` index; integration uses
  `notion_integration_<pid>`, deleted afterwards
- The default `notion` index — where real synced data lives — is never written to
- The Notion API is never called: unit tests use a mock transport, integration
  tests a local stand-in server

Note that `MEILI_INDEX` and `NOTION_API_BASE` exist for exactly this. If you point
them at something real, the isolation is gone.

## Writing tests

New test files go in `tests/unit/` named after the module they cover. Shared
fixtures live in `tests/conftest.py`:

| Fixture | Gives you |
|---|---|
| `store` | Isolated SQLite database, returns the `db` module |
| `index` | Disposable Meilisearch index, torn down after |
| `seed_pages(store)` | Realistic sample workspace |
| `meili_required` | Marker that skips when Meilisearch is absent |

Integration tests need `@pytest.mark.integration` (the file-level `pytestmark`
handles it) so tier selection keeps working.

Async tests need no decorator — `asyncio_mode = "auto"` is set under
`[tool.pytest.ini_options]` in `pyproject.toml`.

Because that config lives at the project root, pytest finds it from anywhere
(config discovery walks upward), so these are all equivalent:

```bash
pytest                # from the project root
pytest tests          # from the project root
cd tests && pytest
```

## Coverage

The unit tier must keep **100% statement and branch coverage** of `app/`. The
threshold lives in `pyproject.toml` and CI fails the build below it:

```bash
.venv/bin/python -m pytest tests/unit -q --cov
```

It is enforced against `tests/unit` only, because the integration tier runs the
app in a **separate process** — coverage cannot see inside it. Integration tests
prove behaviour end to end rather than contributing coverage.

Genuinely unreachable code is marked `# pragma: no cover` (or `no branch`) with a
comment saying why, so the exemptions stay few and reviewable rather than the
threshold being quietly lowered. There is currently one, in `app/main.py`.

## Linting

```bash
.venv/bin/python -m ruff check app/ tests/
.venv/bin/python -m ruff check --fix app/ tests/   # apply the safe fixes
node --check web/app.js
```

`ruff` comes with the `[dev]` extra and covers pyflakes, pycodestyle, import
sorting, bugbear and more; it is configured under `[tool.ruff]` in
`pyproject.toml`.

Ruff is a tool, not an oracle. `app/search.py` carries a `# noqa: SIM118` because
its suggested rewrite (`"key" in row` instead of `"key" in row.keys()`) is wrong
for `sqlite3.Row`, whose `__contains__` tests values rather than keys — taking
the suggestion would silently blank the location facet.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | What it does |
|---|---|
| Tests | ruff, the 100% coverage gate, then the integration tier against a real Meilisearch service |
| Docker image | Builds the image, starts the stack, checks health and that the UI is served |
| Windows installer | Compiles the Inno Setup script, so a broken installer is caught here rather than at release |
