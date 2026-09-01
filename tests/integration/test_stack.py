"""Integration tests: the stack as actually shipped, driven over HTTP.

Unlike the per-module tests, nothing here is patched or stubbed. A real uvicorn
process serves the app (same command as the Dockerfile), a real Meilisearch
indexes it, and a stand-in Notion server stands where the real API would.

This is the test that would catch a broken Dockerfile command, a bad static
mount, or a sync pipeline that only works in-process.

Run: python -m pytest tests/integration -q
Skipped automatically when Meilisearch isn't reachable.
"""

import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
MEILI_URL = os.environ.get("MEILI_URL", "http://127.0.0.1:7700")
MEILI_KEY = os.environ.get("MEILI_MASTER_KEY", "")
TOKEN = "ntn_integration_test_key"
# Never the default "notion" index: that is where real user data lives.
TEST_INDEX = f"notion_integration_{os.getpid()}"


def meili_reachable() -> bool:
    try:
        httpx.get(f"{MEILI_URL}/health", timeout=2).raise_for_status()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not meili_reachable(),
        reason=f"Meilisearch not reachable at {MEILI_URL}",
    ),
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=2)
            return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError(f"{url} never came up")


# --- a stand-in Notion API ------------------------------------------------

def rt(text):
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def page(pid, title, *, parent=None, edited="2026-05-01T00:00:00.000Z", props=None):
    properties = {"Name": {"type": "title", "title": rt(title)}}
    properties.update(props or {})
    return {
        "object": "page", "id": pid, "url": f"https://notion.so/{pid}",
        "created_time": "2025-01-01T00:00:00.000Z", "last_edited_time": edited,
        "archived": False, "properties": properties,
        "parent": ({"type": "page_id", "page_id": parent} if parent
                   else {"type": "workspace", "workspace": True}),
    }


def para(text, bid):
    return {"object": "block", "id": bid, "type": "paragraph",
            "has_children": False, "paragraph": {"rich_text": rt(text)}}


STATUS = {"type": "status", "status": {"name": "Planning"}}
TAGS = {"type": "multi_select", "multi_select": [{"name": "Travel"}]}

# Deliberately split across two pages of results to exercise cursor handling.
WORKSPACE_PAGE_1 = [
    page("aaaa1111", "Travel"),
    page("bbbb2222", "Lisbon Trip", parent="aaaa1111", props={"Status": STATUS, "Tags": TAGS}),
]
WORKSPACE_PAGE_2 = [
    page("cccc3333", "Quarterly Budget"),
]
BLOCKS = {
    "aaaa1111": [para("Everything about trips.", "b1")],
    "bbbb2222": [para("Staying in Alfama near the tram.", "b2"),
                 para("Budget is 1200 euros.", "b3")],
    "cccc3333": [para("Q1 spending review.", "b4")],
}


class FakeNotionHandler(BaseHTTPRequestHandler):
    """Implements just the endpoints sync uses."""

    def log_message(self, *args):
        pass  # keep pytest output clean

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._auth_ok():
            return self._send({"message": "API token is invalid.",
                               "code": "unauthorized"}, 401)

        if path == "/v1/users/me":
            return self._send({"object": "user", "name": "Search Bot",
                               "bot": {"workspace_name": "Luna's Workspace"}})

        if path.startswith("/v1/blocks/"):
            block_id = path.split("/")[3]
            return self._send({"results": BLOCKS.get(block_id, []), "has_more": False})

        self._send({"message": "not found"}, 404)

    def do_POST(self):
        if not self._auth_ok():
            return self._send({"message": "API token is invalid.",
                               "code": "unauthorized"}, 401)

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or "{}")

        if urlparse(self.path).path == "/v1/search":
            if body.get("start_cursor") == "page2":
                return self._send({"results": WORKSPACE_PAGE_2, "has_more": False})
            return self._send({"results": WORKSPACE_PAGE_1,
                               "has_more": True, "next_cursor": "page2"})

        self._send({"message": "not found"}, 404)


@pytest.fixture(scope="module")
def fake_notion():
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeNotionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()


@pytest.fixture(scope="module")
def app_server(fake_notion, tmp_path_factory):
    """The real app, started the same way the container starts it."""
    port = free_port()
    data_dir = tmp_path_factory.mktemp("integration-data")

    env = {
        **os.environ,
        "NOTIONSEARCH_DATA": str(data_dir),
        "NOTION_API_BASE": fake_notion,
        "MEILI_URL": MEILI_URL,
        "MEILI_MASTER_KEY": MEILI_KEY,
        "MEILI_INDEX": TEST_INDEX,
        "APP_PASSWORD": "",
        "PYTHONPATH": str(ROOT),
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        wait_for(f"{base}/health")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        # Drop the throwaway index so repeat runs start clean.
        with contextlib.suppress(Exception):
            httpx.delete(
                f"{MEILI_URL}/indexes/{TEST_INDEX}",
                headers={"Authorization": f"Bearer {MEILI_KEY}"} if MEILI_KEY else {},
                timeout=10,
            )


@pytest.fixture(scope="module")
def synced(app_server):
    """Walk the real user journey once, then let the tests assert against it."""
    with httpx.Client(base_url=app_server, timeout=60) as c:
        assert c.get("/api/status").json()["configured"] is False

        res = c.post("/api/config/notion", json={"token": TOKEN})
        assert res.status_code == 200, res.text

        assert c.post("/api/sync", json={"mode": "incremental"}).status_code == 200

        deadline = time.time() + 60
        while time.time() < deadline:
            status = c.get("/api/sync/status").json()
            if not status["running"]:
                break
            time.sleep(0.3)
        else:
            pytest.fail("sync never finished")

        assert status["status"] == "ok", status
    return app_server


@pytest.fixture
def client(synced):
    with httpx.Client(base_url=synced, timeout=30) as c:
        yield c


def titles(res):
    return [h["title"] for h in res.json()["hits"]]


# --- the journey ----------------------------------------------------------

def test_server_is_healthy(app_server):
    assert httpx.get(f"{app_server}/health").json()["ok"] is True


def test_ui_and_assets_are_served(app_server):
    """A real server, not TestClient: catches a broken static mount."""
    assert "NotionSearch" in httpx.get(f"{app_server}/").text
    for asset in ("/static/app.js", "/static/styles.css"):
        assert httpx.get(f"{app_server}{asset}").status_code == 200


def test_bad_key_is_rejected_by_the_real_stack(app_server):
    res = httpx.post(f"{app_server}/api/config/notion", json={"token": "ntn_wrong_key_x"})
    assert res.status_code == 400
    assert "check you copied" in res.json()["error"].lower()


def test_sync_pulled_every_page_across_pagination(client):
    """Three pages arrived over two cursor pages of search results."""
    status = client.get("/api/status").json()
    assert status["configured"] is True
    assert status["workspace"] == "Luna's Workspace"
    assert status["page_count"] == 3


def test_block_content_was_indexed(client):
    assert "Lisbon Trip" in titles(client.get("/api/search", params={"q": "alfama"}))


def test_search_over_http_tolerates_typos(client):
    assert "Quarterly Budget" in titles(client.get("/api/search", params={"q": "quartrly budgt"}))


def test_breadcrumbs_were_built(client):
    hit = client.get("/api/search", params={"q": "Lisbon"}).json()["hits"][0]
    assert hit["breadcrumb"] == "Travel"


def test_facets_from_notion_properties(client):
    res = client.get("/api/search", params=[("q", ""), ("facet", "Status:Planning")])
    assert titles(res) == ["Lisbon Trip"]


def test_page_detail_over_http(client):
    detail = client.get("/api/page/bbbb2222").json()
    assert detail["title"] == "Lisbon Trip"
    assert "1200 euros" in detail["content"]


def test_second_sync_is_incremental(client):
    """Nothing changed in Notion, so no page should be re-read."""
    assert client.post("/api/sync", json={"mode": "incremental"}).status_code == 200

    deadline = time.time() + 60
    while time.time() < deadline:
        status = client.get("/api/sync/status").json()
        if not status["running"]:
            break
        time.sleep(0.3)

    assert status["status"] == "ok"
    assert status["updated"] == 0, "unchanged pages must not be re-fetched"


def test_sync_history_recorded(client):
    history = client.get("/api/sync/history").json()
    assert len(history) >= 2
    assert all(run["status"] == "ok" for run in history)
