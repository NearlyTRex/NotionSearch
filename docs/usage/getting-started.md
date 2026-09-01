# Getting started

Two commands, then everything else happens in your browser.

## Windows: one file

Download `NotionSearch-<version>-Setup.exe` from the
[latest release](https://github.com/NearlyTRex/NotionSearch/releases) and run it.

The installer checks what your PC needs and fills in the gaps:

- If **Docker Desktop** is missing it downloads and installs it for you. Windows
  will ask your permission part way through, and it may ask you to restart
  afterwards.
- If **hardware virtualisation** is switched off in your BIOS/UEFI it says so,
  because Docker cannot run without it and no installer can turn it on for you.

Then launch **NotionSearch** from the Start Menu and skip to
[step 2](#2-connect-notion).

> Windows SmartScreen will warn that the publisher is unknown, because the
> installer is not code-signed. Choose **More info** → **Run anyway**.

## 1. Start it (Linux, macOS, or from source)

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

### Create a connection

In Notion, open **Settings** → **Developer tools** → the **Connections** tab.
(<https://www.notion.so/my-integrations> also lands there.)

Click **+ New connection**, name it something like *Search*, choose your workspace,
and make sure it is allowed to **read content**.

For **Auth type**, choose **Access token** — the non-OAuth option. OAuth is for
apps that many different people install; this one only needs to reach your own
workspace.

### Copy its access token

Open the connection you just created and copy its **access token**. It starts with
`ntn_`. Paste it into the setup page.

The token is checked against Notion before it is saved, so you'll know immediately
if it's wrong.

> **Not the "Personal access tokens" tab.** It sits directly beside
> **Connections** and sounds like the same thing, but it is a different mechanism
> and will not work here.

### Connect your pages to it

**This is the step almost everyone misses, and nothing works without it.**
Creating the connection gives it access to *nothing*. Notion shows it only the
pages you connect to it, one time each.

In Notion:

1. Open a page
2. Click the **•••** menu at the top right
3. Choose **Connections** (some versions say **Add connections**)
4. Pick your connection and confirm

Everything nested inside that page comes along automatically, so connecting a
handful of top-level pages usually covers your whole workspace.

**Pages under "Private" have to be connected individually** — owning them is not
enough.

> If the app connects successfully but finds nothing, this is almost always why.
> Run `python3 scripts/notion-doctor.py` to confirm: it reports exactly how many
> pages Notion is willing to share.

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
