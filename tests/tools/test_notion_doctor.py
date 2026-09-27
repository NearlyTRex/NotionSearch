"""Tests for scripts/notion-doctor.py.

The doctor is what people run when a sync finds nothing, so its advice has to be
right in exactly the situations that are hard to reproduce by hand: a rejected
key, an unreachable Notion, a key that works but sees nothing.

Everything here is offline: Notion is replaced with canned responses.
"""

import importlib.util
import io
import json
import sqlite3
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "notion-doctor.py"


def _load():
    """Import the script by path: its filename is not a valid module name."""
    spec = importlib.util.spec_from_file_location("notion_doctor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["notion_doctor"] = module
    spec.loader.exec_module(module)
    return module


doctor = _load()


def page(title, parent="workspace", pid="p1"):
    return {"object": "page", "id": pid, "parent": {"type": parent},
            "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}}}


def database(title):
    return {"object": "database", "title": [{"plain_text": title}]}


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("https://api.notion.com/v1/x", code, "err", {}, io.BytesIO(body))


class FakeNotion:
    """Stands in for doctor.call, answering by path."""

    def __init__(self, me=None, search=None, blocks=None):
        self.me = me if me is not None else {"name": "Search", "bot": {"workspace_name": "Home"}}
        # A list of search result pages, returned one per request.
        self.search = search if search is not None else [{"results": []}]
        self.blocks = blocks if blocks is not None else {"results": []}
        self.calls = []

    def __call__(self, path, token, method="GET", body=None):
        self.calls.append((path, method, body))
        if path == "/users/me":
            return self._answer(self.me)
        if path == "/search":
            return self._answer(self.search[min(len(self.calls) - 2, len(self.search) - 1)])
        return self._answer(self.blocks)

    @staticmethod
    def _answer(value):
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def notion(monkeypatch):
    def install(**kwargs):
        fake = FakeNotion(**kwargs)
        monkeypatch.setattr(doctor, "call", fake)
        return fake
    return install


@pytest.fixture(autouse=True)
def no_saved_key(monkeypatch, tmp_path):
    """Never read a real saved key from the developer's data folder."""
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setenv("NOTIONSEARCH_DATA", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)


def run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["notion-doctor.py", *args])
    return doctor.main()


# --- finding the key ------------------------------------------------------

def save_key(folder, value):
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "notionsearch.db")
    conn.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT)")
    if value is not None:
        conn.execute("INSERT INTO config VALUES ('notion_token', ?)", (value,))
    conn.commit()
    conn.close()


def test_saved_key_is_read_from_the_data_folder(tmp_path):
    save_key(tmp_path / "data", json.dumps("ntn_saved"))
    assert doctor.saved_token() == "ntn_saved"


def test_no_database_means_no_saved_key():
    assert doctor.saved_token() is None


def test_database_without_a_key(tmp_path):
    save_key(tmp_path / "data", None)
    assert doctor.saved_token() is None


def test_unreadable_saved_key_is_skipped(tmp_path):
    save_key(tmp_path / "data", "not json")
    assert doctor.saved_token() is None


def test_database_without_a_config_table_is_skipped(tmp_path):
    (tmp_path / "data").mkdir()
    sqlite3.connect(tmp_path / "data" / "notionsearch.db").close()
    assert doctor.saved_token() is None


def test_falls_back_to_the_default_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIONSEARCH_DATA", str(tmp_path / "elsewhere"))
    save_key(tmp_path / "data", json.dumps("ntn_default"))
    assert doctor.saved_token() == "ntn_default"


def test_no_key_anywhere(monkeypatch, capsys):
    assert run(monkeypatch) == 1
    assert "No API key found" in capsys.readouterr().out


def test_key_from_the_environment(monkeypatch, notion):
    fake = notion(search=[{"results": [page("A")]}])
    monkeypatch.setenv("NOTION_TOKEN", "ntn_env")
    assert run(monkeypatch) == 0
    assert fake.calls[0][0] == "/users/me"


def test_saved_key_is_used_when_nothing_else_is_given(tmp_path, monkeypatch, notion):
    save_key(tmp_path / "data", json.dumps("ntn_saved"))
    notion(search=[{"results": [page("A")]}])
    assert run(monkeypatch) == 0


# --- talking to Notion ----------------------------------------------------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_call_sends_the_key_version_and_body(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen["url"], seen["method"], seen["data"] = req.full_url, req.method, req.data
        seen["headers"] = dict(req.headers)
        return FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(doctor.urllib.request, "urlopen", urlopen)
    assert doctor.call("/search", "ntn_x", method="POST", body={"page_size": 1}) == {"ok": True}
    assert seen["url"] == "https://api.notion.com/v1/search"
    assert seen["method"] == "POST"
    assert json.loads(seen["data"]) == {"page_size": 1}
    assert seen["headers"]["Authorization"] == "Bearer ntn_x"
    assert seen["headers"]["Notion-version"] == doctor.NOTION_VERSION


def test_call_without_a_body_sends_none(monkeypatch):
    seen = {}

    def urlopen(req, timeout):
        seen["data"] = req.data
        return FakeResponse(b"{}")

    monkeypatch.setattr(doctor.urllib.request, "urlopen", urlopen)
    doctor.call("/users/me", "ntn_x")
    assert seen["data"] is None


@pytest.mark.parametrize("item,title", [
    (database("Reading List"), "Reading List"),
    ({"object": "database", "title": []}, "Untitled database"),
    (page("Trip"), "Trip"),
    ({"object": "page", "properties": {"Name": {"type": "title", "title": []}}}, "Untitled"),
    ({"object": "page", "properties": {"Tags": {"type": "multi_select"}}}, "Untitled"),
    ({"object": "page"}, "Untitled"),
])
def test_title_of(item, title):
    assert doctor.title_of(item) == title


# --- the diagnosis --------------------------------------------------------

def test_rejected_key_explains_what_to_check(monkeypatch, notion, capsys):
    notion(me=http_error(401, b'{"code": "unauthorized"}'))
    assert run(monkeypatch, "--token", "ntn_bad") == 1
    out = capsys.readouterr().out
    assert "HTTP 401" in out
    assert "whole Internal Integration Secret" in out
    assert "unauthorized" in out


def test_other_http_errors_are_reported(monkeypatch, notion, capsys):
    notion(me=http_error(500))
    assert run(monkeypatch, "--token", "ntn_x") == 1
    out = capsys.readouterr().out
    assert "HTTP 500" in out
    assert "Internal Integration Secret" not in out


def test_unreachable_notion(monkeypatch, notion, capsys):
    notion(me=urllib.error.URLError("no route to host"))
    assert run(monkeypatch, "--token", "ntn_x") == 1
    assert "Could not reach Notion: no route to host" in capsys.readouterr().out


def test_missing_workspace_details_are_tolerated(monkeypatch, notion, capsys):
    notion(me={}, search=[{"results": [page("A")]}])
    assert run(monkeypatch, "--token", "ntn_x") == 0
    out = capsys.readouterr().out
    assert "unnamed" in out
    assert "(not reported)" in out


def test_nothing_shared_explains_how_to_connect_pages(monkeypatch, notion, capsys):
    notion(search=[{"results": []}])
    assert run(monkeypatch, "--token", "ntn_x") == 1
    out = capsys.readouterr().out
    assert "Nothing is shared with the integration yet" in out
    assert "Connections" in out


def test_follows_pagination(monkeypatch, notion, capsys):
    fake = notion(search=[
        {"results": [page("A")], "has_more": True, "next_cursor": "c2"},
        {"results": [database("B")], "has_more": False},
    ])
    assert run(monkeypatch, "--token", "ntn_x") == 0
    searches = [body for path, _, body in fake.calls if path == "/search"]
    assert searches == [{"page_size": 100}, {"page_size": 100, "start_cursor": "c2"}]
    out = capsys.readouterr().out
    assert "pages     : 1" in out
    assert "databases : 1" in out


def test_has_more_without_a_cursor_stops(monkeypatch, notion):
    fake = notion(search=[{"results": [page("A")], "has_more": True, "next_cursor": None}])
    assert run(monkeypatch, "--token", "ntn_x") == 0
    assert sum(path == "/search" for path, _, _ in fake.calls) == 1


def test_pagination_is_bounded(monkeypatch, notion):
    fake = notion(search=[{"results": [page("A")], "has_more": True, "next_cursor": "again"}])
    assert run(monkeypatch, "--token", "ntn_x") == 0
    assert sum(path == "/search" for path, _, _ in fake.calls) == 51


def test_sample_is_capped_at_ten(monkeypatch, notion, capsys):
    pages = [page(f"Page {n}", pid=f"p{n}") for n in range(12)]
    notion(search=[{"results": pages}], blocks={"results": [{}, {}]})
    assert run(monkeypatch, "--token", "ntn_x") == 0
    out = capsys.readouterr().out
    assert "Page 9" in out
    assert "Page 10" not in out
    assert "and 2 more" in out
    assert "has 2 block(s)" in out
    assert "looks empty" not in out


def test_small_sample_has_no_more_line(monkeypatch, notion, capsys):
    notion(search=[{"results": [page("Only")]}])
    assert run(monkeypatch, "--token", "ntn_x") == 0
    out = capsys.readouterr().out
    assert "[page] Only" in out
    assert "more (use --verbose" not in out
    assert "looks empty" in out


def test_verbose_lists_everything(monkeypatch, notion, capsys):
    pages = [page(f"Page {n}", parent="page_id", pid=f"p{n}") for n in range(12)]
    notion(search=[{"results": [database("Books"), *pages, {"object": "page"}]}])
    assert run(monkeypatch, "--token", "ntn_x", "--verbose") == 0
    out = capsys.readouterr().out
    assert "[database] Books" in out
    assert "Page 11   (parent: page_id)" in out
    assert "Untitled   (parent: ?)" in out


def test_only_databases_skips_the_content_check(monkeypatch, notion, capsys):
    fake = notion(search=[{"results": [database("Books")]}])
    assert run(monkeypatch, "--token", "ntn_x") == 0
    assert not any(path.startswith("/blocks/") for path, _, _ in fake.calls)
    assert "Notion looks healthy" in capsys.readouterr().out


def test_unreadable_content_points_at_the_capability(monkeypatch, notion, capsys):
    notion(search=[{"results": [page("A")]}], blocks=http_error(403))
    assert run(monkeypatch, "--token", "ntn_x") == 1
    out = capsys.readouterr().out
    assert "Could NOT read page content (HTTP 403)" in out
    assert "Read content" in out
