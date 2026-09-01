# Configuration

Everything has a working default. You only need this file if you want to change
something.

```bash
cp docker/.env.example docker/.env
# edit docker/.env
docker compose up -d
```

## Settings

| Variable | Default | What it does |
|---|---|---|
| `PORT` | `8080` | The address becomes `localhost:PORT` |
| `APP_PASSWORD` | *(empty)* | Blank means no login. Set it to require a password |
| `MEILI_MASTER_KEY` | `notionsearch_local_key_change_me` | Internal search engine key |
| `PUID` / `PGID` | `1000` | The user the container runs as |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose logs |

### PORT

If something else already uses 8080:

```bash
PORT=9000
```

Then use <http://localhost:9000>.

### APP_PASSWORD

Left blank, anyone with access to your computer can open the page. That's usually
right for a personal machine, since the app is only reachable from that computer
anyway.

Set it to require a password:

```bash
APP_PASSWORD=something-only-you-know
```

The password is checked against an environment variable, not stored in the
database. Changing it signs everyone out.

### PUID / PGID

Which user the container runs as. This keeps `data/` owned by you rather than root,
so you can back it up without `sudo`.

`1000` is the first user account on most Linux desktops and is almost always
correct. Check with:

```bash
id -u    # PUID
id -g    # PGID
```

Not used on Docker Desktop for Mac or Windows.

### MEILI_MASTER_KEY

Authenticates the app to its own search engine. It never leaves your machine, and
the search engine isn't reachable from outside the container network, so the default
is fine. Change it if you like:

```bash
MEILI_MASTER_KEY=any-long-random-string
```

Changing it after data is indexed means the index has to be rebuilt — use
Settings → **Full rebuild**.

## Applying changes

```bash
docker compose up -d
```

Compose recreates only what changed.

## Environment variables the app reads

These are set for you by `docker-compose.yml`. You'd only touch them running
outside Docker.

| Variable | Default | Purpose |
|---|---|---|
| `NOTIONSEARCH_DATA` | `./data` | Where the SQLite database lives |
| `MEILI_URL` | `http://localhost:7700` | Search engine address |
| `MEILI_INDEX` | `notion` | Index name. Overridden in tests |
| `NOTION_API_BASE` | `https://api.notion.com/v1` | Notion API root. Overridden in tests |
