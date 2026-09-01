"""Minimal Notion API client.

Notion allows roughly 3 requests/second per integration and answers 429 with a
Retry-After header. A full workspace sync is thousands of requests, so every
call goes through a throttle and a bounded retry.
"""

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger("notionsearch.notion")

# Overridable so integration tests can point at a stand-in Notion server.
API_BASE = os.environ.get("NOTION_API_BASE", "https://api.notion.com/v1")
NOTION_VERSION = "2022-06-28"

# Notion's documented average is 3 req/s. Leave headroom so bursts don't trip it.
MIN_INTERVAL = 0.34
MAX_RETRIES = 5


class NotionError(Exception):
    """Notion returned an error we cannot recover from."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class NotionClient:
    def __init__(self, token: str, timeout: float = 60.0, transport=None):
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=timeout,
            transport=transport,  # tests inject a MockTransport here
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def close(self):
        await self._client.aclose()

    async def _throttle(self):
        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def request(self, method: str, path: str, **kwargs) -> dict:
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            await self._throttle()
            try:
                resp = await self._client.request(method, path, **kwargs)
            except httpx.RequestError as exc:
                # Network hiccup: back off and retry.
                last_error = exc
                await asyncio.sleep(min(2**attempt, 10))
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", 2))
                log.warning("Notion rate limited, sleeping %.1fs", retry_after)
                await asyncio.sleep(min(retry_after, 60))
                continue

            if resp.status_code >= 500:
                last_error = NotionError(f"Notion server error {resp.status_code}", resp.status_code)
                await asyncio.sleep(min(2**attempt, 10))
                continue

            if resp.status_code >= 400:
                # 4xx other than 429 won't fix itself; surface it immediately.
                try:
                    body = resp.json()
                    message = body.get("message", resp.text)
                    code = body.get("code")
                except Exception:
                    message, code = resp.text, None
                raise NotionError(message, resp.status_code, code)

            return resp.json()

        raise NotionError(
            f"Notion request failed after {MAX_RETRIES} attempts: {last_error}",
        )

    # --- endpoints --------------------------------------------------------

    async def me(self) -> dict:
        """Validate the token and identify the integration."""
        return await self.request("GET", "/users/me")

    async def search_all(self, page_size: int = 100):
        """Yield every page and database shared with this integration.

        Sorted by last edit descending so incremental syncs meet fresh content
        first and can stop early.
        """
        cursor = None
        while True:
            payload = {
                "page_size": page_size,
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            }
            if cursor:
                payload["start_cursor"] = cursor

            data = await self.request("POST", "/search", json=payload)
            for item in data.get("results", []):
                yield item

            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")
            if not cursor:
                return

    async def block_children(self, block_id: str, page_size: int = 100):
        """Yield the direct children of a block or page."""
        cursor = None
        while True:
            params = {"page_size": page_size}
            if cursor:
                params["start_cursor"] = cursor

            try:
                data = await self.request("GET", f"/blocks/{block_id}/children", params=params)
            except NotionError as exc:
                # A single unreadable / deleted block must not kill the sync.
                if exc.status in (400, 403, 404):
                    log.info("Skipping children of %s: %s", block_id, exc.message)
                    return
                raise

            for block in data.get("results", []):
                yield block

            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")
            if not cursor:
                return

    async def get_page(self, page_id: str) -> dict:
        return await self.request("GET", f"/pages/{page_id}")

    async def get_database(self, database_id: str) -> dict:
        return await self.request("GET", f"/databases/{database_id}")
