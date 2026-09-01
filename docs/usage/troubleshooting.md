# Troubleshooting

> `docker compose` commands below run from the `docker/` folder
> (`cd NotionSearch/docker`). Paths like `data/` are relative to the project root.

## The page won't load at all

Check both services are running:

```bash
docker compose ps
```

Both should say `healthy`. If `notionsearch-api` is missing or restarting:

```bash
docker compose logs --tail=50 api
```

If the port is already taken by something else, change it — see
[Configuration](../reference/configuration.md).

## It connected, but finds nothing

Almost always the sharing step. Creating a Notion integration does **not** give it
access to anything; you have to connect pages to it explicitly.

In Notion: open a top-level page → **•••** → **Connections** → **Connect to** → your
integration. Then press **Sync**.

Check Settings — if "Pages indexed" is 0 after a successful sync, nothing is shared
yet.

## "Notion rejected that key"

- Make sure you copied the **Internal Integration Secret**, not the integration ID
- Copy the whole thing — it starts `ntn_` or `secret_`
- If you regenerated the secret in Notion, the old one stops working. Paste the new
  one in Settings

## Sync is slow

Expected on the first run. Notion limits how fast any integration can read, at
roughly three requests a second, and a large workspace is thousands of requests.

Later syncs only re-read pages whose content changed, so they finish in seconds.
You can keep searching while one runs.

## Sync failed partway

Press **Sync** again. It picks up where it left off — pages already read aren't
re-fetched.

If a specific page fails repeatedly, the sync skips it and keeps going, so one
broken page won't block everything else. The log names it:

```bash
docker compose logs api | grep -i "content fetch failed"
```

## Search says the engine isn't reachable

The app is up but Meilisearch isn't. Right after `docker compose up` this can
appear for a few seconds while it starts.

If it persists:

```bash
docker compose ps
docker compose logs --tail=30 meilisearch
docker compose restart meilisearch
```

## Results are stale, missing, or wrong

Settings → **Full rebuild**. This re-reads every page from Notion and rebuilds the
index from scratch. Slower than a normal sync, but fixes almost anything.

The search index can always be rebuilt from local data, so nothing is lost.

## Deleted pages still appear

They disappear on the next sync. NotionSearch removes anything Notion stops
returning, which covers both deleted pages and pages you've unshared from the
integration.

## "unable to open database file"

The container couldn't write to the `data/` folder. It normally runs as whoever
owns that folder, so this usually means the folder is missing or owned by someone
unexpected.

```bash
ls -lan data/            # who owns it?
sudo chown -R "$(id -u):$(id -g)" data/
cd docker && docker compose up -d
```

If `data/` doesn't exist at all, recreate it and restart — it ships with the
project, so a stray `rm -rf` is the usual cause:

```bash
mkdir -p data && cd docker && docker compose up -d
```

To force a specific user instead, set `PUID` and `PGID` in `docker/.env` — see
[Configuration](../reference/configuration.md).

## Starting completely fresh

```bash
cd docker && docker compose down -v
rm -f ../data/notionsearch.db*
docker compose up -d
```

Your Notion content is never affected by anything here.
