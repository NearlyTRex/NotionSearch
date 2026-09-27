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

Search tests need it. This starts one at the version `docker/docker-compose.yml`
ships, then runs the command you give it against it:

```bash
.github/scripts/with-meilisearch.sh .venv/bin/python -m pytest -q
```

It is left running for the next run; `docker stop notionsearch-test-meili` stops
it. CI uses the same script, so the tests always run against the Meilisearch that
ships, including right after Dependabot bumps it.

Without it, those tests **skip** and the run prints:

```text
!!!!! Meilisearch was NOT running: search behaviour was not tested !!!!!
```

That banner matters. Search is the point of the project, and a green run that
skipped it is not a passing build.

### In CI

`with-meilisearch.sh` sets `REQUIRE_MEILI=1`, which turns skipping into a hard
error: an unreachable server fails the run instead of quietly skipping the
search tests.

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

Every line of Python in the repository must keep **100% statement and branch
coverage**: the app (`app/`), the scripts users run (`scripts/`), and the tooling
CI runs (`.github/scripts/`). The sources and threshold live under
`[tool.coverage]` in `pyproject.toml`, and CI fails the build below it, on every
Python version it tests:

```bash
.github/scripts/with-meilisearch.sh .venv/bin/python -m pytest tests/unit tests/tools -q --cov
```

The run page shows the coverage table, with any missing lines, in the job
summary.

It is enforced against `tests/unit` and `tests/tools` only, because the
integration tier runs the app in a **separate process** — coverage cannot see
inside it. Integration tests prove behaviour end to end rather than contributing
coverage.

Genuinely unreachable code is marked `# pragma: no cover` (or `no branch`) with a
comment saying why, so the exemptions stay few and reviewable rather than the
threshold being quietly lowered. There is currently one, in `app/main.py`.

## Linting

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff check --fix .   # apply the safe fixes
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

`.github/workflows/ci.yml` runs on every push and pull request. Every job is a
reusable workflow from the shared
[NearlyTRex/Workflows](https://github.com/NearlyTRex/Workflows) library, pinned
to a commit; improvements to them belong there, so every repo gets them. What is
specific to this project is passed in as scripts from `.github/scripts/`.

| Job | What it does |
|---|---|
| Python 3.12, Python 3.14 | ruff, the integration tier against the Meilisearch that ships, then the unit and tooling tiers under the 100% coverage gate. 3.12 is the oldest version supported; 3.14 is what the container runs |
| Lint | shellcheck, Markdown (rules in `.markdownlint.yaml`) and JSON |
| Docker stack | Builds the image and starts the stack until both services report healthy, then checks the UI is served and the app is not running as root |
| Windows installer | Compiles the Inno Setup script and silently installs it, so a broken installer is caught here rather than at release |

### Steps live in scripts, not in YAML

Anything with real logic lives in `.github/scripts/`, and the workflow just calls
it. Only one-liners stay inline.

That is not tidiness for its own sake — it means you can run **exactly what CI
runs**, locally:

```bash
.github/scripts/with-meilisearch.sh CMD     # run CMD against a test Meilisearch
.github/scripts/shellcheck-all.sh          # every shell script in the repo
.github/scripts/check-endpoints.sh          # UI and static assets, against a running stack
.github/scripts/check-not-root.sh           # the app dropped root, against a running stack
```

```powershell
.github\scripts\smoke-test-installer.ps1
```

It also makes them lintable, and it removes a class of bug that YAML-embedded
scripts invite: quoting and escaping that is only exercised on a runner. Both
kinds have already bitten this repo — a PowerShell here-string that silently
terminated a YAML block, and string concatenation inside a PowerShell array
literal that split one line into three. Neither was visible until the script was
run on its own.

### Linting the workflows themselves

```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```

In CI, zizmor audits them for security problems as part of the Security
workflow.
