# Getting started

Two commands, then everything else happens in your browser.

## 1. Start it

```bash
git clone https://github.com/NearlyTRex/NotionSearch.git
cd NotionSearch/docker
docker compose up -d
```

The Compose file lives in the `docker/` folder, so that is where you run
`docker compose` commands from. To stay at the project root instead, use
`docker compose -f docker/docker-compose.yml ...`.

The first run downloads and builds the images, which takes a few minutes. After
that it starts in seconds.

Open **<http://localhost:8080>**.

If you don't have Docker yet, see [Installing Docker](../install/installing-docker.md).

## 2. Connect Notion

The page walks you through this, and it takes about two minutes.

### Create an integration

Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) and click
**New integration**. Give it a name like *Search*, choose your workspace, and make
sure **Read content** is enabled under capabilities.

### Copy the secret

On the integration's page find **Internal Integration Secret**, click **Show**, then
**Copy**. It starts with `ntn_` or `secret_`. Paste it into the setup page.

The key is checked against Notion before it is saved, so you'll know immediately if
it's wrong.

### Share your pages with it

**This is the step almost everyone misses.** Creating the integration is not enough —
Notion shows it nothing until you explicitly share pages with it.

In Notion:

1. Open a top-level page
2. Click the **•••** menu at the top right
3. Choose **Connections** → **Connect to**
4. Pick your integration

Everything nested inside that page comes along automatically, so sharing a handful
of top-level pages usually covers your whole workspace.

> If the app connects successfully but finds nothing, this is almost always why.

## 3. Sync

The first sync starts on its own once your key is accepted. You can watch the
progress bar at the top of the page.

How long it takes depends on the size of your workspace — Notion limits how fast
anyone can read from it, so a few thousand pages takes a few minutes. Every sync
after this one is much faster, because only pages that actually changed get
re-read.

You can keep using the search box while a sync runs.

## 4. Search

Type in the box. Results appear as you type.

See [Searching](searching.md) for what the filters do and how to get the most out
of it.

---

- Day-to-day commands: [Command reference](cli-commands.md)
- Changing the port or adding a password: [Configuration](../reference/configuration.md)
- Something not working: [Troubleshooting](troubleshooting.md)
