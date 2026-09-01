"""Tests for app/search.py — document mapping, filters, and live queries.

The query tests run against a real Meilisearch and are skipped without one,
because typo tolerance and ranking are exactly what a stub would fake away.
"""

import json
import sqlite3

import pytest
from conftest import meili_required, seed_pages

from app import search


def row(**overrides) -> sqlite3.Row:
    """Build a pages-table row like the SQL join in sync produces."""
    base = {
        "id": "1234abcd-1111-2222-3333-444455556666", "object": "page",
        "title": "Lisbon Trip", "content": "Flights booked", "property_text": "",
        "breadcrumb": "Travel", "url": "https://notion.so/x", "icon": "✈️",
        "parent_id": "parent-1", "parent_title": "Travel",
        "properties": json.dumps({"Status": "Planning", "Tags": ["Travel", "2026"]}),
        "archived": 0, "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": "2026-03-01T12:00:00.000Z",
    }
    base.update(overrides)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(f'? AS "{k}"' for k in base)
    return conn.execute(f"SELECT {cols}", tuple(base.values())).fetchone()


# --- document mapping -----------------------------------------------------

def test_document_id_strips_dashes():
    """Meilisearch primary keys allow no dashes; notion_id keeps the real one."""
    doc = search.to_document(row())
    assert doc["id"] == doc["notion_id"].replace("-", "")
    assert "-" not in doc["id"]
    assert doc["notion_id"] == "1234abcd-1111-2222-3333-444455556666"


def test_properties_become_facet_strings():
    doc = search.to_document(row())
    assert set(doc["facets"]) == {"Status:Planning", "Tags:Travel", "Tags:2026"}


def test_boolean_property_facets_read_naturally():
    doc = search.to_document(row(properties=json.dumps({"Done": True, "Draft": False})))
    assert "Done:Yes" in doc["facets"] and "Draft:No" in doc["facets"]


def test_empty_property_values_are_dropped():
    doc = search.to_document(row(properties=json.dumps({"A": "", "B": None, "C": "x"})))
    assert doc["facets"] == ["C:x"]


def test_malformed_properties_json_does_not_raise():
    assert search.to_document(row(properties="not json{"))["facets"] == []


def test_timestamps_parsed_for_sorting():
    doc = search.to_document(row())
    assert doc["last_edited_ts"] > doc["created_ts"] > 0


def test_bad_timestamps_become_zero():
    doc = search.to_document(row(last_edited_time="garbage", created_time=None))
    assert doc["last_edited_ts"] == 0 and doc["created_ts"] == 0


def test_missing_title_falls_back():
    assert search.to_document(row(title=""))["title"] == "Untitled"


# --- filter construction --------------------------------------------------

def test_archived_excluded_by_default():
    assert "archived = false" in search.build_filter()
    assert "archived = false" not in search.build_filter(include_archived=True)


def test_object_filter_ignores_junk_values():
    assert any("object" in c for c in search.build_filter(obj="page"))
    assert not any("object" in c for c in search.build_filter(obj="'; DROP"))


def test_same_property_facets_are_grouped_for_or():
    clauses = search.build_filter(facets=["Tags:A", "Tags:B", "Status:Done"])
    groups = [c for c in clauses if isinstance(c, list)]
    assert sorted(len(g) for g in groups) == [1, 2]


def test_quotes_in_values_are_escaped():
    """A page titled with a quote must not break the filter expression."""
    clause = search.build_filter(parent_title='My "Best" Notes')[1]
    assert '\\"Best\\"' in clause


def test_unknown_time_range_is_ignored():
    assert search.build_filter(edited_within="fortnight") == ["archived = false"]


def test_time_range_produces_lower_bound():
    clause = [c for c in search.build_filter(edited_within="week") if "last_edited_ts" in str(c)]
    assert clause and clause[0].startswith("last_edited_ts >")


# --- live queries ---------------------------------------------------------

@pytest.fixture
def seeded(store, index):
    seed_pages(store)
    rows = store.get_conn().execute(
        """SELECT p.*, COALESCE(parent.title, '') AS parent_title
           FROM pages p LEFT JOIN pages parent ON parent.id = p.parent_id"""
    ).fetchall()
    task = index.add_documents([index.to_document(r) for r in rows])
    index.wait_for([task])
    return index


def titles(result) -> list[str]:
    return [h["title"] for h in result["hits"]]


@meili_required
def test_exact_match(seeded):
    assert "Lisbon Trip 2026" in titles(seeded.query("Lisbon"))


@meili_required
def test_prefix_match(seeded):
    assert "Lisbon Trip 2026" in titles(seeded.query("lisb"))


@meili_required
def test_body_text_is_searchable(seeded):
    assert "Lisbon Trip 2026" in titles(seeded.query("alfama tram"))


@meili_required
@pytest.mark.parametrize("typo,expected", [
    ("Lisban", "Lisbon Trip 2026"),
    ("quarterly budgt", "Quarterly Budget"),
    ("budgett", "Quarterly Budget"),
    ("readng list", "Reading List"),
])
def test_typo_tolerance(seeded, typo, expected):
    assert expected in titles(seeded.query(typo))


@meili_required
def test_accents_ignored(seeded):
    assert "Café Résumé Notes" in titles(seeded.query("cafe resume"))


@meili_required
def test_property_only_match(seeded):
    """A page whose only match is in its properties is still findable."""
    assert "Untitled meeting" in titles(seeded.query("Sam Rivera"))


@meili_required
def test_title_outranks_body(seeded):
    """'Budget' is in Lisbon's body but is Quarterly Budget's title."""
    assert titles(seeded.query("Budget"))[0] == "Quarterly Budget"


@meili_required
def test_object_filter(seeded):
    assert titles(seeded.query("", obj="database")) == ["Reading List"]


@meili_required
def test_facet_filter(seeded):
    assert set(titles(seeded.query("", facets=["Status:Planning"]))) == {
        "Lisbon Trip 2026", "Untitled meeting"}


@meili_required
def test_same_property_facets_or(seeded):
    assert set(titles(seeded.query("", facets=["Tags:Travel", "Tags:Finance"]))) == {
        "Lisbon Trip 2026", "Quarterly Budget"}


@meili_required
def test_different_property_facets_and(seeded):
    assert titles(seeded.query("", facets=["Status:Planning", "Tags:Finance"])) == []


@meili_required
def test_parent_filter_scopes_to_section(seeded):
    assert set(titles(seeded.query("", parent_title="Travel"))) == {
        "Lisbon Trip 2026", "Café Résumé Notes"}


@meili_required
def test_edited_within_narrows(seeded):
    assert set(titles(seeded.query("", edited_within="week"))) == {
        "Lisbon Trip 2026", "Untitled meeting"}


@meili_required
def test_empty_query_lists_recent_first(seeded):
    assert titles(seeded.query(""))[0] == "Untitled meeting"


@meili_required
@pytest.mark.parametrize("sort_by,first", [
    ("recent", "Untitled meeting"),
    ("oldest", "Travel"),
    ("title", "Café Résumé Notes"),
])
def test_sorting(seeded, sort_by, first):
    assert titles(seeded.query("", sort_by=sort_by))[0] == first


@meili_required
def test_highlight_uses_escapable_sentinels(seeded):
    """Not real <mark> tags: the client escapes HTML before substituting."""
    formatted = json.dumps(seeded.query("Lisbon")["hits"][0]["_formatted"])
    assert search.HL_PRE in formatted and "<mark>" not in formatted


@meili_required
def test_facet_distribution_returned(seeded):
    facets = seeded.query("")["facetDistribution"]
    assert facets["facets"]["Status:Planning"] == 2


@meili_required
def test_paging_does_not_repeat(seeded):
    first = titles(seeded.query("", limit=2, offset=0))
    second = titles(seeded.query("", limit=2, offset=2))
    assert not set(first) & set(second)


@meili_required
def test_deleted_documents_disappear(seeded):
    seeded.wait_for(seeded.delete_documents(["33333333-3333-3333-3333-333333333333"]))
    assert "Quarterly Budget" not in titles(seeded.query("budget"))


# --- failure paths --------------------------------------------------------

class Boom(Exception):
    pass


def test_healthy_is_false_when_the_engine_is_unreachable(monkeypatch):
    monkeypatch.setattr(search, "client", lambda: (_ for _ in ()).throw(Boom("down")))
    assert search.healthy() is False


def test_ensure_index_tolerates_an_existing_index(monkeypatch):
    """Every boot after the first hits this path."""
    from meilisearch.errors import MeilisearchError

    calls = {"settings": 0}

    class FakeIndex:
        def update_settings(self, _settings):
            calls["settings"] += 1

    class FakeClient:
        def create_index(self, *a, **k):
            raise MeilisearchError("index_already_exists")

        def index(self, _uid):
            return FakeIndex()

    monkeypatch.setattr(search, "client", FakeClient)
    search.ensure_index()
    assert calls["settings"] == 1


def test_ensure_index_reraises_other_errors(monkeypatch):
    from meilisearch.errors import MeilisearchError

    class FakeClient:
        def create_index(self, *a, **k):
            raise MeilisearchError("invalid_api_key")

    monkeypatch.setattr(search, "client", FakeClient)
    with pytest.raises(MeilisearchError):
        search.ensure_index()


def test_add_documents_of_nothing_is_a_no_op(monkeypatch):
    monkeypatch.setattr(search, "client", lambda: (_ for _ in ()).throw(Boom("must not call")))
    assert search.add_documents([]) is None


def test_delete_documents_of_nothing_is_a_no_op(monkeypatch):
    monkeypatch.setattr(search, "client", lambda: (_ for _ in ()).throw(Boom("must not call")))
    assert search.delete_documents([]) == []


def test_wait_for_skips_none_and_survives_failures(monkeypatch):
    waited = []

    class FakeClient:
        def wait_for_task(self, uid, timeout_in_ms=None):
            waited.append(uid)
            if uid == 99:
                raise Boom("task timed out")

    monkeypatch.setattr(search, "client", FakeClient)
    # None entries are skipped; a failing wait is logged, not raised.
    search.wait_for([None, 1, 99])
    assert waited == [1, 99]


def test_clear_index_tolerates_a_missing_index(monkeypatch):
    from meilisearch.errors import MeilisearchError

    created = {"n": 0}

    class FakeIndex:
        def delete(self):
            raise MeilisearchError("index_not_found")

        def update_settings(self, _s):
            created["n"] += 1

    class FakeClient:
        def index(self, _uid):
            return FakeIndex()

        def create_index(self, *a, **k):
            return None

    monkeypatch.setattr(search, "client", FakeClient)
    search.clear_index()
    assert created["n"] == 1


def test_stats_returns_a_dict_or_empty(monkeypatch):
    class Stats:
        def __init__(self):
            self.numberOfDocuments = 7

    class FakeIndex:
        def get_stats(self):
            return Stats()

    class FakeClient:
        def index(self, _uid):
            return FakeIndex()

    monkeypatch.setattr(search, "client", FakeClient)
    assert search.stats()["numberOfDocuments"] == 7

    monkeypatch.setattr(search, "client", lambda: (_ for _ in ()).throw(Boom("down")))
    assert search.stats() == {}


def test_healthy_is_true_when_the_engine_answers(monkeypatch):
    class FakeClient:
        def health(self):
            return {"status": "available"}

    monkeypatch.setattr(search, "client", FakeClient)
    assert search.healthy() is True


def test_delete_documents_skips_chunks_without_a_task_id(monkeypatch):
    """A client that returns no task id must not put None into the wait list."""
    class NoTask:
        pass

    class FakeIndex:
        def delete_documents(self, filter=None):
            return NoTask()

    class FakeClient:
        def index(self, _uid):
            return FakeIndex()

    monkeypatch.setattr(search, "client", FakeClient)
    assert search.delete_documents(["a", "b"]) == []


def test_query_without_filters_or_sort(monkeypatch):
    """A plain relevance query on a non-empty term sets neither filter nor sort."""
    captured = {}

    class FakeIndex:
        def search(self, q, opts):
            captured["q"] = q
            captured["opts"] = opts
            return {"hits": []}

    class FakeClient:
        def index(self, _uid):
            return FakeIndex()

    monkeypatch.setattr(search, "client", FakeClient)
    search.query("lisbon", sort_by="relevance", include_archived=True)

    assert "filter" not in captured["opts"], "no filters were requested"
    assert "sort" not in captured["opts"], "relevance on a real term needs no sort"
