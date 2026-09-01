# NotionSearch

[![CI](https://github.com/NearlyTRex/NotionSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/NearlyTRex/NotionSearch/actions/workflows/ci.yml)
[![Release](https://github.com/NearlyTRex/NotionSearch/actions/workflows/release.yml/badge.svg)](https://github.com/NearlyTRex/NotionSearch/actions/workflows/release.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](docs/develop/testing.md)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)

Notion's search is bad at finding things you half-remember. This runs a real search
engine over your own Notion content, on your own machine.

It syncs every page your integration can see into a local database, indexes it with
[Meilisearch](https://www.meilisearch.com/), and gives you a page at `localhost:8080`
to search it — with typo tolerance, filters, and instant results.

Nothing leaves your computer except the calls to Notion's own API.

## Quick start

You need [Docker](docs/install/installing-docker.md). Then:

```bash
git clone https://github.com/NearlyTRex/NotionSearch.git
cd NotionSearch/docker
docker compose up -d
```

Open **<http://localhost:8080>** and follow the three steps it shows you.

> Note the `cd NotionSearch/docker` — the Compose file lives there, so that's where
> you run `docker compose` commands from. If you'd rather stay at the project root,
> use `docker compose -f docker/docker-compose.yml ...` instead.

That's the whole install. The first sync starts on its own once you paste your
Notion key.

> **The step everyone misses:** creating a Notion connection isn't enough — you
> have to connect your pages to it. In Notion: **•••** → **Connections** → pick your
> connection. Nested pages come along automatically, but pages under **Private**
> must each be connected.
> Full walkthrough: [Getting started](docs/usage/getting-started.md).

## What it does

- **Finds things you can't spell.** `quarterly budgt` finds *Quarterly Budget*;
  `lisban` finds *Lisbon Trip*
- **Searches everything** — page text, nested blocks, callouts, table rows, image
  captions, and database properties like Status and Tags
- **Filters** by when you edited something, where it lives, or its properties
- **Syncs incrementally** — the first run reads your whole workspace, later ones
  only what changed
- **Stays local** — one SQLite file you can back up, bound to `127.0.0.1`

## Documentation

| | |
|---|---|
| [Installing Docker](docs/install/installing-docker.md) | One-time setup |
| [Getting started](docs/usage/getting-started.md) | Connect Notion and sync |
| [Searching](docs/usage/searching.md) | Filters and search behaviour |
| [Command reference](docs/usage/cli-commands.md) | Start, stop, update, back up |
| [Troubleshooting](docs/usage/troubleshooting.md) | When something breaks |
| [Configuration](docs/reference/configuration.md) | Port, password, settings |
| [HTTP API](docs/reference/api.md) | Every endpoint |
| [Architecture](docs/reference/architecture.md) | How it works |
| [Development](docs/develop/setup.md) · [Testing](docs/develop/testing.md) · [Releasing](docs/develop/releasing.md) | Contributing |

Everything is indexed in [docs/README.md](docs/README.md).

## How it works

```
Notion API  ──sync──>  SQLite (source of truth)  ──index──>  Meilisearch
                            │                                     │
                            └──────────>  FastAPI  <──────────────┘
                                             │
                                        Web UI (localhost:8080)
```

SQLite holds everything; the Meilisearch index is derived and disposable. A rebuild
never has to re-download from Notion, and losing the index costs nothing.

## Your data

Everything — synced pages, settings, your API key — lives in
`data/notionsearch.db`. Back up that one file and you've backed up everything.

The web UI binds to `127.0.0.1`, so it's reachable only from this computer. The
database is owner-only, and the container runs as you rather than root.

## Licence

MIT — see [LICENSE](LICENSE).
