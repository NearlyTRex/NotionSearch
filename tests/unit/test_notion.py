"""Tests for app/notion.py — retries, rate limiting, and pagination."""

import time

import httpx
import pytest

from app.notion import NotionClient, NotionError


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch):
    """Keep the rate limiter's behaviour but drop the wall-clock cost."""
    monkeypatch.setattr("app.notion.MIN_INTERVAL", 0.001)


def client_with(handler) -> NotionClient:
    return NotionClient("ntn_test", transport=httpx.MockTransport(handler))


async def call(client, *args, **kwargs):
    try:
        return await client.request(*args, **kwargs)
    finally:
        await client.close()


# --- error handling -------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_request_returns_json():
    c = client_with(lambda r: httpx.Response(200, json={"ok": True}))
    assert await call(c, "GET", "/users/me") == {"ok": True}


@pytest.mark.asyncio
async def test_sends_auth_and_version_headers():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})

    await call(client_with(handler), "GET", "/users/me")
    assert seen["authorization"] == "Bearer ntn_test"
    assert seen["notion-version"] == "2022-06-28"


@pytest.mark.asyncio
async def test_401_raises_immediately_with_message():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"message": "API token is invalid.",
                                         "code": "unauthorized"})

    c = client_with(handler)
    with pytest.raises(NotionError) as exc:
        await call(c, "GET", "/users/me")

    assert exc.value.status == 401
    assert exc.value.code == "unauthorized"
    assert "invalid" in exc.value.message
    assert len(calls) == 1, "4xx must not be retried"


@pytest.mark.asyncio
async def test_429_is_retried_after_backoff():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"done": True})

    assert await call(client_with(handler), "GET", "/x") == {"done": True}
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_500_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.notion.asyncio.sleep", _no_sleep)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500 if len(calls) == 1 else 200, json={"ok": 1})

    assert await call(client_with(handler), "GET", "/x") == {"ok": 1}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("app.notion.asyncio.sleep", _no_sleep)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={})

    with pytest.raises(NotionError):
        await call(client_with(handler), "GET", "/x")
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_network_error_is_retried(monkeypatch):
    monkeypatch.setattr("app.notion.asyncio.sleep", _no_sleep)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"ok": 1})

    assert await call(client_with(handler), "GET", "/x") == {"ok": 1}


async def _no_sleep(_seconds):
    return None


# --- rate limiting --------------------------------------------------------

@pytest.mark.asyncio
async def test_requests_are_spaced_apart(monkeypatch):
    monkeypatch.setattr("app.notion.MIN_INTERVAL", 0.05)
    c = client_with(lambda r: httpx.Response(200, json={}))

    start = time.monotonic()
    for _ in range(4):
        await c.request("GET", "/x")
    elapsed = time.monotonic() - start
    await c.close()

    # Four calls at 50ms spacing cannot finish in under ~150ms.
    assert elapsed >= 0.14, elapsed


# --- pagination -----------------------------------------------------------

@pytest.mark.asyncio
async def test_search_all_follows_cursors():
    def handler(request):
        body = request.read().decode()
        if "cursor2" in body:
            return httpx.Response(200, json={
                "results": [{"id": "c"}], "has_more": False, "next_cursor": None})
        if "cursor1" in body:
            return httpx.Response(200, json={
                "results": [{"id": "b"}], "has_more": True, "next_cursor": "cursor2"})
        return httpx.Response(200, json={
            "results": [{"id": "a"}], "has_more": True, "next_cursor": "cursor1"})

    c = client_with(handler)
    ids = [item["id"] async for item in c.search_all()]
    await c.close()
    assert ids == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_search_all_sorts_by_last_edited():
    seen = {}

    def handler(request):
        import json as _json
        seen.update(_json.loads(request.read()))
        return httpx.Response(200, json={"results": [], "has_more": False})

    c = client_with(handler)
    [item async for item in c.search_all()]
    await c.close()
    assert seen["sort"]["timestamp"] == "last_edited_time"
    assert seen["sort"]["direction"] == "descending"


@pytest.mark.asyncio
async def test_search_all_stops_when_cursor_missing():
    """has_more=True with no cursor must terminate, not loop forever."""
    def handler(request):
        return httpx.Response(200, json={
            "results": [{"id": "a"}], "has_more": True, "next_cursor": None})

    c = client_with(handler)
    ids = [item["id"] async for item in c.search_all()]
    await c.close()
    assert ids == ["a"]


@pytest.mark.asyncio
async def test_block_children_paginates():
    def handler(request):
        if "start_cursor=next" in str(request.url):
            return httpx.Response(200, json={
                "results": [{"id": "b2"}], "has_more": False})
        return httpx.Response(200, json={
            "results": [{"id": "b1"}], "has_more": True, "next_cursor": "next"})

    c = client_with(handler)
    ids = [b["id"] async for b in c.block_children("page-1")]
    await c.close()
    assert ids == ["b1", "b2"]


@pytest.mark.parametrize("status", [400, 403, 404])
@pytest.mark.asyncio
async def test_block_children_skips_unreadable_blocks(status):
    """One deleted or permission-denied block must not abort a whole sync."""
    c = client_with(lambda r: httpx.Response(status, json={"message": "nope"}))
    blocks = [b async for b in c.block_children("gone")]
    await c.close()
    assert blocks == []


@pytest.mark.asyncio
async def test_block_children_propagates_real_errors(monkeypatch):
    monkeypatch.setattr("app.notion.asyncio.sleep", _no_sleep)
    c = client_with(lambda r: httpx.Response(500, json={}))
    with pytest.raises(NotionError):
        [b async for b in c.block_children("x")]
    await c.close()


# --- remaining endpoints and edge cases -----------------------------------

@pytest.mark.asyncio
async def test_async_context_manager_closes_the_client():
    c = client_with(lambda r: httpx.Response(200, json={"ok": 1}))
    async with c as opened:
        assert opened is c
        assert await c.request("GET", "/x") == {"ok": 1}
    assert c._client.is_closed


@pytest.mark.asyncio
async def test_me_returns_the_integration():
    payload = {"object": "user", "name": "Search Bot",
               "bot": {"workspace_name": "Luna's Workspace"}}
    c = client_with(lambda r: httpx.Response(200, json=payload))
    assert (await call(c, "GET", "/users/me")) == payload

    c2 = client_with(lambda r: httpx.Response(200, json=payload))
    me = await c2.me()
    await c2.close()
    assert me["bot"]["workspace_name"] == "Luna's Workspace"


@pytest.mark.asyncio
async def test_get_page_and_database():
    def handler(request):
        return httpx.Response(200, json={"id": str(request.url).rsplit("/", 1)[-1]})

    c = client_with(handler)
    assert (await c.get_page("page-1"))["id"] == "page-1"
    assert (await c.get_database("db-1"))["id"] == "db-1"
    await c.close()


@pytest.mark.asyncio
async def test_non_json_error_body_still_raises_cleanly():
    """Notion occasionally returns HTML from a proxy rather than JSON."""
    c = client_with(lambda r: httpx.Response(403, text="<html>Forbidden</html>"))
    with pytest.raises(NotionError) as exc:
        await call(c, "GET", "/x")
    assert exc.value.status == 403
    assert "Forbidden" in exc.value.message
    assert exc.value.code is None


@pytest.mark.asyncio
async def test_block_children_stops_when_cursor_missing():
    """has_more=True with no cursor must terminate rather than loop forever."""
    def handler(request):
        return httpx.Response(200, json={
            "results": [{"id": "b1"}], "has_more": True, "next_cursor": None})

    c = client_with(handler)
    ids = [b["id"] async for b in c.block_children("p")]
    await c.close()
    assert ids == ["b1"]
