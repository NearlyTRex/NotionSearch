#!/usr/bin/env python3
"""Ask Notion what it is willing to show your integration.

When a sync finds nothing, the question is almost always "is Notion returning
anything at all?" This answers that directly, without involving the database,
the search engine, or the sync pipeline.

Usage:
    python3 scripts/notion-doctor.py                  # uses your saved key
    NOTION_TOKEN=ntn_xxx python3 scripts/notion-doctor.py
    python3 scripts/notion-doctor.py --token ntn_xxx
    python3 scripts/notion-doctor.py --verbose        # list every page found

Needs no dependencies beyond the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def saved_token() -> str | None:
    """Read the key the app stored, so you don't have to paste it again."""
    for candidate in (
        Path(os.environ.get("NOTIONSEARCH_DATA", "data")) / "notionsearch.db",
        Path("data/notionsearch.db"),
    ):
        if not candidate.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT value FROM config WHERE key = 'notion_token'"
            ).fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
        except (sqlite3.Error, json.JSONDecodeError):
            continue
    return None


def call(path: str, token: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def title_of(item: dict) -> str:
    if item.get("object") == "database":
        parts = item.get("title") or []
        return "".join(p.get("plain_text", "") for p in parts) or "Untitled database"
    for prop in (item.get("properties") or {}).values():
        if prop.get("type") == "title":
            text = "".join(p.get("plain_text", "") for p in (prop.get("title") or []))
            if text:
                return text
    return "Untitled"


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose what Notion shares with you.")
    parser.add_argument("--token", help="Integration secret (else NOTION_TOKEN, else saved)")
    parser.add_argument("--verbose", action="store_true", help="list every object found")
    args = parser.parse_args()

    token = args.token or os.environ.get("NOTION_TOKEN") or saved_token()
    if not token:
        print("No API key found.\n")
        print("Pass one with --token, set NOTION_TOKEN, or configure the app first.")
        return 1

    print("=" * 62)
    print("  Notion connection check")
    print("=" * 62)

    # 1. Is the key valid?
    try:
        me = call("/users/me", token)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        print(f"\n  Notion rejected the key (HTTP {exc.code}).")
        if exc.code == 401:
            print("  Check you copied the whole Internal Integration Secret.")
        print(f"  {detail[:200]}")
        return 1
    except urllib.error.URLError as exc:
        print(f"\n  Could not reach Notion: {exc.reason}")
        return 1

    bot = me.get("bot") or {}
    print(f"\n  Key is valid.")
    print(f"    integration : {me.get('name') or 'unnamed'}")
    print(f"    workspace   : {bot.get('workspace_name') or '(not reported)'}")

    # 2. What will it actually return?
    pages, databases, cursor, requests = [], [], None, 0
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = call("/search", token, method="POST", body=payload)
        requests += 1
        for item in result.get("results", []):
            (databases if item.get("object") == "database" else pages).append(item)
        if not result.get("has_more") or not result.get("next_cursor"):
            break
        cursor = result["next_cursor"]
        if requests > 50:
            break

    total = len(pages) + len(databases)
    print(f"\n  Notion is sharing {total} object(s) with this integration:")
    print(f"    pages     : {len(pages)}")
    print(f"    databases : {len(databases)}")

    if total == 0:
        print("\n" + "=" * 62)
        print("  Nothing is shared with the integration yet.")
        print("=" * 62)
        print("""
  The key works, but Notion is deliberately showing it nothing. Creating an
  integration does not give it access to any pages — you have to connect each
  top-level page to it by hand, once:

      1. Open a top-level page in Notion
      2. Click the  •••  menu at the top right
      3. Choose  Connections  ->  Connect to
      4. Pick your integration, and confirm

  Everything nested inside that page is then included automatically, so a few
  top-level pages usually cover the whole workspace.

  Two things that catch people out:

    - A page you created inside someone else's shared space may need that
      space's owner to allow the connection.
    - Private pages (only you can see them) still need connecting individually;
      being the owner is not enough.

  Do that, then run this again — you should see a count above zero.
""")
        return 1

    if args.verbose:
        print("\n  Everything Notion is sharing:")
        for item in databases:
            print(f"    [database] {title_of(item)}")
        for item in pages:
            parent = (item.get("parent") or {}).get("type", "?")
            print(f"    [page]     {title_of(item)}   (parent: {parent})")
    else:
        print("\n  A sample:")
        for item in (databases + pages)[:10]:
            kind = "database" if item.get("object") == "database" else "page"
            print(f"    [{kind}] {title_of(item)}")
        if total > 10:
            print(f"    ... and {total - 10} more (use --verbose to list all)")

    # 3. Can we read the content, not just the titles?
    if pages:
        sample = pages[0]
        try:
            blocks = call(f"/blocks/{sample['id']}/children?page_size=10", token)
            count = len(blocks.get("results", []))
            print(f"\n  Content is readable: '{title_of(sample)}' has {count} block(s).")
            if count == 0:
                print("    (that page looks empty, which is fine)")
        except urllib.error.HTTPError as exc:
            print(f"\n  Could NOT read page content (HTTP {exc.code}).")
            print("  The integration may lack the 'Read content' capability.")
            return 1

    print("\n  Notion looks healthy. If the app still shows nothing, press Sync")
    print("  and check Settings for the last sync result.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
