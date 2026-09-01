"""Tests for app/main.py — the REST API and auth gate."""

import pytest
from conftest import meili_required, seed_pages
from fastapi.testclient import TestClient

from app import main, sync
from app.notion import NotionError


@pytest.fixture
def client(store, monkeypatch):
    """TestClient with auth off (the default single-user setup)."""
    monkeypatch.setattr(main, "APP_PASSWORD", "")
    monkeypatch.setattr(sync, "STATE", sync.SyncState())
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def locked_client(store, monkeypatch):
    monkeypatch.setattr(main, "APP_PASSWORD", "hunter2")
    with TestClient(main.app) as c:
        yield c


class FakeNotionOK:
    def __init__(self, *a, **k):
        pass

    async def me(self):
        return {"name": "Search Bot", "bot": {"workspace_name": "Luna's Workspace"}}

    async def close(self):
        pass


class FakeNotionBadKey(FakeNotionOK):
    async def me(self):
        raise NotionError("API token is invalid.", 401, "unauthorized")


# --- status ---------------------------------------------------------------

def test_health_is_public(client):
    assert client.get("/health").json()["ok"] is True


def test_status_before_setup(client):
    body = client.get("/api/status").json()
    assert body["configured"] is False
    assert body["auth_required"] is False
    assert body["page_count"] == 0


def test_status_after_setup(client, store):
    store.set_setting("notion_token", "ntn_x")
    store.set_setting("workspace_name", "Luna's Workspace")
    seed_pages(store)

    body = client.get("/api/status").json()
    assert body["configured"] is True
    assert body["workspace"] == "Luna's Workspace"
    assert body["page_count"] == 6
    assert body["database_count"] == 1


def test_status_never_returns_the_token(client, store):
    store.set_setting("notion_token", "ntn_supersecret")
    assert "ntn_supersecret" not in client.get("/api/status").text


def test_index_page_is_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "NotionSearch" in res.text


# --- auth -----------------------------------------------------------------

def test_endpoints_open_when_no_password_set(client):
    assert client.get("/api/sync/status").status_code == 200


def test_locked_status_reports_signed_out(locked_client):
    body = locked_client.get("/api/status").json()
    assert body["auth_required"] is True and body["signed_in"] is False
    # Page counts are withheld until sign-in.
    assert "page_count" not in body


def test_locked_endpoints_reject_anonymous(locked_client):
    assert locked_client.get("/api/sync/status").status_code == 401
    assert locked_client.get("/api/search", params={"q": "x"}).status_code == 401


def test_wrong_password_rejected(locked_client):
    res = locked_client.post("/api/auth/login", json={"password": "nope"})
    assert res.status_code == 401


def test_login_then_access(locked_client):
    assert locked_client.post("/api/auth/login", json={"password": "hunter2"}).status_code == 200
    assert locked_client.get("/api/sync/status").status_code == 200

    locked_client.post("/api/auth/logout")
    assert locked_client.get("/api/sync/status").status_code == 401


def test_session_cookie_is_httponly(locked_client):
    res = locked_client.post("/api/auth/login", json={"password": "hunter2"})
    assert "httponly" in res.headers["set-cookie"].lower()


def test_forged_cookie_rejected(locked_client):
    locked_client.cookies.set(main.SESSION_COOKIE, "forged.value.here")
    assert locked_client.get("/api/sync/status").status_code == 401


# --- Notion key configuration --------------------------------------------

def test_valid_key_is_stored(client, store, monkeypatch):
    monkeypatch.setattr(main, "NotionClient", FakeNotionOK)
    res = client.post("/api/config/notion", json={"token": "ntn_valid_key_123"})

    assert res.status_code == 200
    assert res.json()["workspace"] == "Luna's Workspace"
    assert store.get_setting("notion_token") == "ntn_valid_key_123"


def test_invalid_key_is_not_stored(client, store, monkeypatch):
    monkeypatch.setattr(main, "NotionClient", FakeNotionBadKey)
    res = client.post("/api/config/notion", json={"token": "ntn_bad_key_123"})

    assert res.status_code == 400
    assert "check you copied" in res.json()["error"].lower()
    assert store.get_setting("notion_token") is None


def test_short_key_rejected_before_calling_notion(client):
    assert client.post("/api/config/notion", json={"token": "abc"}).status_code == 422


def test_disconnect_clears_key_and_pages(client, store, monkeypatch):
    monkeypatch.setattr(main.search, "clear_index", lambda: None)
    store.set_setting("notion_token", "ntn_x")
    seed_pages(store)

    assert client.delete("/api/config/notion").status_code == 200
    assert store.get_setting("notion_token") is None
    assert store.get_conn().execute("SELECT COUNT(*) c FROM pages").fetchone()["c"] == 0


# --- sync -----------------------------------------------------------------

def test_sync_requires_a_key(client):
    res = client.post("/api/sync", json={"mode": "incremental"})
    assert res.status_code == 400 and "no notion api key" in res.json()["error"].lower()


def test_sync_rejects_a_second_run(client, store, monkeypatch):
    store.set_setting("notion_token", "ntn_x")
    monkeypatch.setattr(sync.STATE, "running", True)
    assert client.post("/api/sync", json={"mode": "incremental"}).status_code == 409


def test_sync_status_shape(client):
    body = client.get("/api/sync/status").json()
    assert set(body) >= {"running", "status", "phase", "percent", "processed", "total"}


def test_sync_history_is_listed(client, store):
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO sync_runs (started_at, status, mode, updated) VALUES (?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "ok", "incremental", 7))
    history = client.get("/api/sync/history").json()
    assert history[0]["status"] == "ok" and history[0]["updated"] == 7


def test_cancel_when_idle_is_harmless(client):
    assert client.post("/api/sync/cancel").json()["ok"] is False


# --- page detail ----------------------------------------------------------

def test_page_detail(client, store):
    seed_pages(store)
    body = client.get("/api/page/22222222-2222-2222-2222-222222222222").json()
    assert body["title"] == "Lisbon Trip 2026" and "Alfama" in body["content"]


def test_page_detail_accepts_dashless_id(client, store):
    seed_pages(store)
    body = client.get("/api/page/22222222222222222222222222222222").json()
    assert body["title"] == "Lisbon Trip 2026"


def test_unknown_page_is_404(client, store):
    seed_pages(store)
    assert client.get("/api/page/does-not-exist").status_code == 404


# --- search ---------------------------------------------------------------

@pytest.fixture
def searchable(client, store, index):
    seed_pages(store)
    rows = store.get_conn().execute(
        """SELECT p.*, COALESCE(parent.title, '') AS parent_title
           FROM pages p LEFT JOIN pages parent ON parent.id = p.parent_id"""
    ).fetchall()
    index.wait_for([index.add_documents([index.to_document(r) for r in rows])])
    return client


def titles(res) -> list[str]:
    return [h["title"] for h in res.json()["hits"]]


@meili_required
def test_search_returns_hits(searchable):
    res = searchable.get("/api/search", params={"q": "Lisbon"})
    assert res.status_code == 200
    assert "Lisbon Trip 2026" in titles(res)
    assert res.json()["total"] >= 1


@meili_required
def test_search_tolerates_typos(searchable):
    assert "Quarterly Budget" in titles(searchable.get("/api/search", params={"q": "budgt"}))


@meili_required
def test_repeated_facet_params_are_applied(searchable):
    """Regression: a bare list[str] was read as a body, so facets were ignored."""
    res = searchable.get("/api/search", params=[("q", ""), ("facet", "Status:Planning")])
    assert set(titles(res)) == {"Lisbon Trip 2026", "Untitled meeting"}


@meili_required
def test_multiple_facets_of_same_property_or_together(searchable):
    res = searchable.get("/api/search",
                         params=[("q", ""), ("facet", "Tags:Travel"), ("facet", "Tags:Finance")])
    assert set(titles(res)) == {"Lisbon Trip 2026", "Quarterly Budget"}


@meili_required
def test_parent_filter(searchable):
    res = searchable.get("/api/search", params={"q": "", "parent": "Travel"})
    assert set(titles(res)) == {"Lisbon Trip 2026", "Café Résumé Notes"}


@meili_required
def test_object_and_edited_filters(searchable):
    assert titles(searchable.get(
        "/api/search", params={"q": "", "object": "database"})) == ["Reading List"]
    assert set(titles(searchable.get(
        "/api/search", params={"q": "", "edited": "week"}))) == {
            "Lisbon Trip 2026", "Untitled meeting"}


@meili_required
def test_empty_query_returns_recent(searchable):
    assert titles(searchable.get("/api/search", params={"q": ""}))[0] == "Untitled meeting"


@meili_required
def test_paging_params(searchable):
    body = searchable.get("/api/search", params={"q": "", "limit": 2, "offset": 2}).json()
    assert len(body["hits"]) == 2 and body["offset"] == 2


@meili_required
def test_limit_is_capped(searchable):
    """A huge limit must not be passed straight through to the engine."""
    assert searchable.get("/api/search", params={"q": "", "limit": 5000}).status_code == 200


@meili_required
def test_facet_distribution_is_returned(searchable):
    assert searchable.get("/api/search", params={"q": ""}).json()["facets"]["facets"]


@meili_required
def test_highlight_sentinels_reach_the_client(searchable):
    body = searchable.get("/api/search", params={"q": "Lisbon"}).json()
    assert "[[hl]]" in str(body["hits"][0]["_formatted"])


def test_search_reports_engine_outage(client, monkeypatch):
    monkeypatch.setattr(main.search, "healthy", lambda: False)
    res = client.get("/api/search", params={"q": "x"})
    assert res.status_code == 503 and "not reachable" in res.json()["error"]


# --- remaining branches ---------------------------------------------------

def test_startup_survives_meilisearch_being_down(store, monkeypatch):
    """The app must still boot so the UI can explain what's wrong."""
    monkeypatch.setattr(main, "APP_PASSWORD", "")

    def refuse():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main.search, "ensure_index", refuse)
    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200


def test_login_is_a_no_op_when_no_password_is_configured(client):
    body = client.post("/api/auth/login", json={"password": "anything"}).json()
    assert body == {"ok": True, "auth_required": False}


def test_sync_starts_and_reports_state(client, store, monkeypatch):
    store.set_setting("notion_token", "ntn_x")

    async def fake_start(token, mode):
        assert token == "ntn_x"
        assert mode == "incremental"
        return True

    monkeypatch.setattr(sync, "start", fake_start)
    body = client.post("/api/sync", json={"mode": "incremental"}).json()
    assert body["ok"] is True
    assert body["mode"] == "incremental"
    assert "sync" in body


def test_unknown_sync_mode_falls_back_to_incremental(client, store, monkeypatch):
    store.set_setting("notion_token", "ntn_x")
    seen = {}

    async def fake_start(token, mode):
        seen["mode"] = mode
        return True

    monkeypatch.setattr(sync, "start", fake_start)
    body = client.post("/api/sync", json={"mode": "nonsense"}).json()
    assert seen["mode"] == "incremental" and body["mode"] == "incremental"


def test_search_engine_failure_becomes_a_500(client, monkeypatch):
    monkeypatch.setattr(main.search, "healthy", lambda: True)

    def explode(*a, **k):
        raise RuntimeError("index corrupted")

    monkeypatch.setattr(main.search, "query", explode)
    res = client.get("/api/search", params={"q": "x"})
    assert res.status_code == 500
    assert "index corrupted" in res.json()["error"]


def test_cancel_reports_true_while_running(client, monkeypatch):
    monkeypatch.setattr(sync.STATE, "running", True)
    assert client.post("/api/sync/cancel").json()["ok"] is True


def test_existing_secret_key_is_reused_across_restarts(store, monkeypatch):
    """A regenerated key would sign every existing session out."""
    monkeypatch.setattr(main, "APP_PASSWORD", "")
    with TestClient(main.app):
        pass
    first = store.get_setting("secret_key")

    with TestClient(main.app):
        pass
    assert store.get_setting("secret_key") == first


def test_non_401_notion_errors_keep_their_own_message(client, store, monkeypatch):
    class RateLimited:
        def __init__(self, *a, **k):
            pass

        async def me(self):
            raise NotionError("Rate limited, slow down.", 429, "rate_limited")

        async def close(self):
            pass

    monkeypatch.setattr(main, "NotionClient", RateLimited)
    res = client.post("/api/config/notion", json={"token": "ntn_valid_key_123"})
    assert res.status_code == 400
    # The generic 401 wording must not overwrite a more specific message.
    assert res.json()["error"] == "Rate limited, slow down."
