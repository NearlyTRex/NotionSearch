"""FastAPI application: REST API + static web UI."""

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field

from . import db, search, sync
from .notion import NotionClient, NotionError

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("notionsearch")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
# Set APP_PASSWORD to require a login; unset means open (fine on localhost).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
SESSION_COOKIE = "notionsearch_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if not db.get_setting("secret_key"):
        db.set_setting("secret_key", secrets.token_hex(32))
    try:
        search.ensure_index()
    except Exception as exc:  # Meilisearch may still be booting.
        log.warning("Meilisearch not ready at startup: %s", exc)
    log.info("NotionSearch ready")
    yield


app = FastAPI(title="NotionSearch", version="0.1.0", lifespan=lifespan)


def serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(db.get_setting("secret_key", "dev-secret"))


def require_auth(request: Request) -> None:
    """No-op when APP_PASSWORD is unset."""
    if not APP_PASSWORD:
        return
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise HTTPException(401, "Not signed in")
    try:
        serializer().loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature as exc:
        raise HTTPException(401, "Session expired") from exc


def notion_token() -> str:
    token = db.get_setting("notion_token")
    if not token:
        raise HTTPException(400, "No Notion API key configured yet")
    return token


# --- models ---------------------------------------------------------------

class LoginBody(BaseModel):
    password: str


class TokenBody(BaseModel):
    token: str = Field(min_length=10)


class SyncBody(BaseModel):
    mode: str = "incremental"


# --- auth -----------------------------------------------------------------

@app.post("/api/auth/login")
def login(body: LoginBody, response: Response):
    if not APP_PASSWORD:
        return {"ok": True, "auth_required": False}
    if not secrets.compare_digest(body.password, APP_PASSWORD):
        raise HTTPException(401, "Incorrect password")
    response.set_cookie(
        SESSION_COOKIE,
        serializer().dumps({"ok": True}),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


# --- status & config ------------------------------------------------------

@app.get("/api/status")
def status(request: Request):
    """Drives the UI's first render: is it set up, signed in, synced?"""
    signed_in = True
    if APP_PASSWORD:
        try:
            require_auth(request)
        except HTTPException:
            signed_in = False

    token = db.get_setting("notion_token")
    payload = {
        "auth_required": bool(APP_PASSWORD),
        "signed_in": signed_in,
        "configured": bool(token),
        "workspace": db.get_setting("workspace_name"),
        "bot_name": db.get_setting("bot_name"),
        "last_sync_at": db.get_setting("last_sync_at"),
        "last_sync_ok": db.get_setting("last_sync_ok"),
        "search_ready": search.healthy(),
        "sync": sync.STATE.snapshot(),
    }
    if signed_in:
        row = db.get_conn().execute(
            "SELECT COUNT(*) c, SUM(object='database') d FROM pages WHERE archived = 0"
        ).fetchone()
        payload["page_count"] = row["c"] or 0
        payload["database_count"] = row["d"] or 0
    return payload


@app.post("/api/config/notion", dependencies=[Depends(require_auth)])
async def set_notion_key(body: TokenBody):
    """Validate the key against Notion before storing it."""
    client = NotionClient(body.token.strip())
    try:
        me = await client.me()
    except NotionError as exc:
        detail = exc.message
        if exc.status == 401:
            detail = "Notion rejected that key. Check you copied the whole Internal Integration Secret."
        raise HTTPException(400, detail) from exc
    finally:
        await client.close()

    bot = me.get("bot") or {}
    workspace = bot.get("workspace_name") or ""
    db.set_setting("notion_token", body.token.strip())
    db.set_setting("bot_name", me.get("name") or "Integration")
    db.set_setting("workspace_name", workspace)

    return {"ok": True, "bot_name": me.get("name"), "workspace": workspace}


@app.delete("/api/config/notion", dependencies=[Depends(require_auth)])
def clear_notion_key():
    for key in ("notion_token", "workspace_name", "bot_name", "last_sync_at", "last_sync_ok"):
        db.delete_setting(key)
    with db.tx() as conn:
        conn.execute("DELETE FROM pages")
    search.clear_index()
    return {"ok": True}


# --- sync -----------------------------------------------------------------

@app.post("/api/sync", dependencies=[Depends(require_auth)])
async def start_sync(body: SyncBody):
    token = notion_token()
    mode = body.mode if body.mode in ("incremental", "full") else "incremental"
    if not await sync.start(token, mode):
        raise HTTPException(409, "A sync is already running")
    return {"ok": True, "mode": mode, "sync": sync.STATE.snapshot()}


@app.post("/api/sync/cancel", dependencies=[Depends(require_auth)])
def cancel_sync():
    return {"ok": sync.request_cancel(), "sync": sync.STATE.snapshot()}


@app.get("/api/sync/status", dependencies=[Depends(require_auth)])
def sync_status():
    return sync.STATE.snapshot()


@app.get("/api/sync/history", dependencies=[Depends(require_auth)])
def sync_history(limit: int = 10):
    rows = db.get_conn().execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (min(limit, 50),)
    ).fetchall()
    return [dict(r) for r in rows]


# --- search ---------------------------------------------------------------

@app.get("/api/search", dependencies=[Depends(require_auth)])
def do_search(
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    sort: str = "relevance",
    parent: str | None = None,
    object: str | None = None,
    edited: str | None = None,
    # Query() is required: a bare list[str] on a GET is read as a request body.
    facet: list[str] | None = Query(None),
    include_archived: bool = False,
):
    if not search.healthy():
        raise HTTPException(503, "Search engine is not reachable")
    try:
        result = search.query(
            q,
            limit=min(limit, 100),
            offset=offset,
            sort_by=sort,
            parent_title=parent,
            obj=object,
            facets=facet,
            edited_within=edited,
            include_archived=include_archived,
        )
    except Exception as exc:
        raise HTTPException(500, f"Search failed: {exc}") from exc

    return {
        "query": q,
        "hits": result.get("hits", []),
        "total": result.get("estimatedTotalHits", 0),
        "processing_ms": result.get("processingTimeMs", 0),
        "facets": result.get("facetDistribution", {}),
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/page/{page_id}", dependencies=[Depends(require_auth)])
def get_page(page_id: str):
    """Full stored record, for the preview pane."""
    row = db.get_conn().execute(
        """
        SELECT p.*, COALESCE(parent.title, '') AS parent_title
        FROM pages p LEFT JOIN pages parent ON parent.id = p.parent_id
        WHERE p.id = ? OR REPLACE(p.id, '-', '') = ?
        """,
        (page_id, page_id.replace("-", "")),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Page not found locally")
    return dict(row)


# --- static UI ------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "search": search.healthy()}


# pragma: no cover on the else branch — this runs once at import, and the false
# case (a deployment with no web/ directory) can only be reached by re-importing
# this module with a patched path, which would leak app state into other tests.
if WEB_DIR.exists():  # pragma: no branch
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.exception_handler(HTTPException)
def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
