# Command reference

Every `docker compose` command runs from the **`docker/` folder**, because that is
where the Compose file lives:

```bash
cd NotionSearch/docker
```

To run them from the project root instead, add `-f docker/docker-compose.yml` to
each one. Commands that touch `data/` are shown from the project root.

## Everyday

| Command | What it does |
|---|---|
| `docker compose up -d` | Start it. Runs in the background |
| `docker compose down` | Stop it. Your synced data is kept |
| `docker compose restart api` | Restart just the app, e.g. after changing `docker/.env` |
| `docker compose ps` | Show whether things are running and healthy |
| `docker compose logs -f api` | Watch what the app is doing. `Ctrl-C` to stop watching |

`-d` means "detached" — it keeps running after you close the terminal. Leave it off
if you'd rather watch the output live.

## Updating

```bash
git pull
cd docker && docker compose up -d --build
```

`--build` rebuilds the app image with the new code. Your synced pages and API key
survive an update.

## Checking on it

```bash
docker compose ps                      # both services should say "healthy"
curl http://localhost:8080/health      # {"ok":true,"search":true}
docker compose logs --tail=50 api      # recent app logs
docker compose logs --tail=50 meilisearch
```

`"search":false` means the app is running but can't reach the search engine. Give it
a few seconds after startup; if it persists see
[Troubleshooting](troubleshooting.md).

## Backups

Everything — synced pages, settings, your API key — lives in one file:

```bash
# from the project root
cp data/notionsearch.db ~/notionsearch-backup.db
```

Do it while the app is stopped (`docker compose down`) for a guaranteed-clean copy.
You don't need `sudo`: the container runs as your own user.

To restore, put the file back and start up again.

## Starting over

Remove your API key and everything synced to this machine, keeping the app
installed — use **Settings → Disconnect Notion** in the web page, or:

```bash
cd docker && docker compose down -v
rm -f ../data/notionsearch.db*
docker compose up -d
```

`-v` also deletes the search index. Nothing here touches your actual Notion
content.

## Removing it completely

```bash
cd docker && docker compose down -v --rmi all
cd ../.. && rm -rf NotionSearch
```

## Running without Docker

For development. See [Development setup](../develop/setup.md).
