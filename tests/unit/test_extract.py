"""Extraction tests against realistic Notion API payloads.

Run: .venv/bin/python -m pytest tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import extract


def rt(text):
    """Build a minimal rich_text array like Notion returns."""
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


# --- block text -----------------------------------------------------------

def test_paragraph():
    block = {"type": "paragraph", "paragraph": {"rich_text": rt("Book flights to Lisbon")}}
    assert extract.block_text(block) == "Book flights to Lisbon"


def test_headings_and_lists():
    cases = [
        ({"type": "heading_1", "heading_1": {"rich_text": rt("Budget")}}, "Budget"),
        ({"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt("Milk")}}, "Milk"),
        ({"type": "numbered_list_item", "numbered_list_item": {"rich_text": rt("First")}}, "First"),
        ({"type": "quote", "quote": {"rich_text": rt("Onward")}}, "Onward"),
    ]
    for block, expected in cases:
        assert extract.block_text(block) == expected


def test_todo_shows_checked_state():
    done = {"type": "to_do", "to_do": {"rich_text": rt("Pack"), "checked": True}}
    todo = {"type": "to_do", "to_do": {"rich_text": rt("Pack"), "checked": False}}
    assert extract.block_text(done) == "[x] Pack"
    assert extract.block_text(todo) == "[ ] Pack"


def test_code_includes_language_and_caption():
    block = {"type": "code", "code": {
        "rich_text": rt("print(1)"), "language": "python", "caption": rt("demo")}}
    text = extract.block_text(block)
    assert "python" in text and "print(1)" in text and "demo" in text


def test_callout_keeps_emoji():
    block = {"type": "callout", "callout": {
        "rich_text": rt("Remember this"), "icon": {"type": "emoji", "emoji": "💡"}}}
    assert extract.block_text(block) == "💡 Remember this"


def test_table_row_joins_cells():
    block = {"type": "table_row", "table_row": {
        "cells": [rt("Lisbon"), rt("€400"), rt("Booked")]}}
    assert extract.block_text(block) == "Lisbon | €400 | Booked"


def test_bookmark_captures_url_and_caption():
    block = {"type": "bookmark", "bookmark": {
        "url": "https://example.com/flights", "caption": rt("cheap flights")}}
    text = extract.block_text(block)
    assert "cheap flights" in text and "https://example.com/flights" in text


def test_image_caption_is_indexed():
    block = {"type": "image", "image": {
        "type": "external", "external": {"url": "https://img.test/a.png"},
        "caption": rt("hotel courtyard")}}
    assert "hotel courtyard" in extract.block_text(block)


def test_equation_and_child_page():
    assert extract.block_text({"type": "equation", "equation": {"expression": "E=mc^2"}}) == "E=mc^2"
    assert extract.block_text(
        {"type": "child_page", "child_page": {"title": "Sub page"}}) == "Sub page"


def test_structural_blocks_are_empty():
    for btype in ("divider", "breadcrumb", "column_list", "table_of_contents"):
        assert extract.block_text({"type": btype, btype: {}}) == ""


def test_unknown_block_type_falls_back_to_rich_text():
    block = {"type": "some_future_block", "some_future_block": {"rich_text": rt("still findable")}}
    assert extract.block_text(block) == "still findable"


def test_malformed_block_does_not_raise():
    assert extract.block_text({}) == ""
    assert extract.block_text({"type": "paragraph"}) == ""
    assert extract.block_text({"type": "paragraph", "paragraph": {}}) == ""


# --- titles and icons -----------------------------------------------------

def test_page_title_from_title_property():
    page = {"object": "page", "properties": {
        "Name": {"type": "title", "title": rt("Lisbon Trip")}}}
    assert extract.title_of(page) == "Lisbon Trip"


def test_database_title():
    db = {"object": "database", "title": rt("Reading List")}
    assert extract.title_of(db) == "Reading List"


def test_untitled_fallbacks():
    assert extract.title_of({"object": "page", "properties": {}}) == "Untitled"
    assert extract.title_of({"object": "database", "title": []}) == "Untitled database"


def test_emoji_icon_only():
    assert extract.icon_of({"icon": {"type": "emoji", "emoji": "✈️"}}) == "✈️"
    assert extract.icon_of({"icon": {"type": "external", "external": {"url": "x"}}}) is None
    assert extract.icon_of({}) is None


# --- properties -----------------------------------------------------------

def test_property_types_render():
    cases = [
        ({"type": "select", "select": {"name": "Done"}}, "Done"),
        ({"type": "status", "status": {"name": "In progress"}}, "In progress"),
        ({"type": "multi_select", "multi_select": [{"name": "A"}, {"name": "B"}]}, "A, B"),
        ({"type": "checkbox", "checkbox": True}, "Yes"),
        ({"type": "number", "number": 42}, "42"),
        ({"type": "url", "url": "https://x.test"}, "https://x.test"),
        ({"type": "date", "date": {"start": "2026-03-01", "end": None}}, "2026-03-01"),
        ({"type": "people", "people": [{"name": "Sam"}]}, "Sam"),
        ({"type": "formula", "formula": {"type": "string", "string": "calc"}}, "calc"),
        ({"type": "unique_id", "unique_id": {"prefix": "TASK", "number": 7}}, "TASK-7"),
    ]
    for prop, expected in cases:
        assert extract.property_value(prop) == expected, prop["type"]


def test_null_properties_are_safe():
    for prop in ({"type": "select", "select": None},
                 {"type": "date", "date": None},
                 {"type": "number", "number": None},
                 {"type": "formula", "formula": None}):
        assert extract.property_value(prop) == ""


def test_flatten_splits_filters_from_text():
    props = {
        "Name": {"type": "title", "title": rt("Trip")},
        "Status": {"type": "status", "status": {"name": "Planning"}},
        "Tags": {"type": "multi_select", "multi_select": [{"name": "Travel"}, {"name": "2026"}]},
        "Done": {"type": "checkbox", "checkbox": False},
        "Notes": {"type": "rich_text", "rich_text": rt("bring adapter")},
    }
    values, text = extract.flatten_properties(props)

    assert values["Status"] == "Planning"
    assert values["Tags"] == ["Travel", "2026"]
    assert values["Done"] is False
    # Free text is searchable but not a facet.
    assert "Notes" not in values
    assert "bring adapter" in text
    # The title is indexed separately, so it must not be duplicated here.
    assert "Trip" not in text


# --- coverage of the remaining block and property shapes ------------------

def test_notion_hosted_file_block_strips_signed_query():
    """Notion's own file URLs are signed and expire; keep only the stable part."""
    block = {"type": "file", "file": {
        "type": "file",
        "file": {"url": "https://s3.notion.so/budget.pdf?X-Amz-Signature=abc123"},
        "name": "budget.pdf",
        "caption": rt("Q1 numbers")}}
    text = extract.block_text(block)
    assert "https://s3.notion.so/budget.pdf" in text
    assert "X-Amz-Signature" not in text
    assert "budget.pdf" in text and "Q1 numbers" in text


def test_link_to_page_is_empty():
    """A reference only: the target page is indexed in its own right."""
    assert extract.block_text(
        {"type": "link_to_page", "link_to_page": {"page_id": "abc"}}) == ""


def test_property_without_a_type_is_empty():
    assert extract.property_value({}) == ""
    assert extract.property_value({"type": None}) == ""


def test_files_property_lists_names():
    prop = {"type": "files", "files": [
        {"name": "itinerary.pdf"}, {"name": "tickets.png"}, {}]}
    assert extract.property_value(prop) == "itinerary.pdf, tickets.png"


def test_formula_date_uses_start():
    prop = {"type": "formula", "formula": {
        "type": "date", "date": {"start": "2026-03-01"}}}
    assert extract.property_value(prop) == "2026-03-01"


def test_formula_number_and_boolean():
    assert extract.property_value(
        {"type": "formula", "formula": {"type": "number", "number": 12}}) == "12"
    assert extract.property_value(
        {"type": "formula", "formula": {"type": "boolean", "boolean": True}}) == "True"
    assert extract.property_value(
        {"type": "formula", "formula": {"type": "number", "number": None}}) == ""


def test_rollup_array_renders_each_entry():
    prop = {"type": "rollup", "rollup": {"type": "array", "array": [
        {"type": "select", "select": {"name": "A"}},
        {"type": "select", "select": {"name": "B"}}]}}
    assert extract.property_value(prop) == "A, B"


def test_rollup_date_and_number_and_empty():
    assert extract.property_value({"type": "rollup", "rollup": {
        "type": "date", "date": {"start": "2026-01-01"}}}) == "2026-01-01"
    assert extract.property_value({"type": "rollup", "rollup": {
        "type": "number", "number": 5}}) == "5"
    assert extract.property_value({"type": "rollup", "rollup": None}) == ""
    assert extract.property_value({"type": "rollup", "rollup": {
        "type": "number", "number": None}}) == ""


def test_timestamp_and_user_properties():
    assert extract.property_value(
        {"type": "created_time", "created_time": "2026-01-01T00:00:00Z"}
    ) == "2026-01-01T00:00:00Z"
    assert extract.property_value(
        {"type": "created_by", "created_by": {"name": "Sam"}}) == "Sam"
    assert extract.property_value(
        {"type": "last_edited_by", "last_edited_by": None}) == ""


def test_unique_id_without_prefix():
    assert extract.property_value(
        {"type": "unique_id", "unique_id": {"prefix": None, "number": 12}}) == "12"
    assert extract.property_value({"type": "unique_id", "unique_id": None}) == ""


def test_relation_is_not_searchable_text():
    """Relations are ids, which would be noise in the index."""
    assert extract.property_value(
        {"type": "relation", "relation": [{"id": "abc"}]}) == ""


def test_unrecognised_property_type_is_empty():
    assert extract.property_value({"type": "some_new_type", "some_new_type": {}}) == ""


def test_people_become_a_filterable_facet():
    values, text = extract.flatten_properties({
        "Owner": {"type": "people", "people": [{"name": "Sam"}, {"name": "Alex"}]},
        "Nobody": {"type": "people", "people": []},
    })
    assert values["Owner"] == ["Sam", "Alex"]
    assert "Nobody" not in values
    assert "Owner: Sam, Alex" in text


# --- branch coverage ------------------------------------------------------

def test_title_search_skips_non_title_and_empty_title_properties():
    """The loop must keep looking past a non-title prop and an empty title."""
    page = {"object": "page", "properties": {
        "Tags": {"type": "multi_select", "multi_select": [{"name": "X"}]},
        "Blank": {"type": "title", "title": []},
        "Name": {"type": "title", "title": rt("Found me")},
    }}
    assert extract.title_of(page) == "Found me"


def test_title_falls_through_when_every_title_is_empty():
    page = {"object": "page", "properties": {
        "Other": {"type": "number", "number": 1},
        "Name": {"type": "title", "title": []},
    }}
    assert extract.title_of(page) == "Untitled"


def test_external_url_block_keeps_its_url_and_skips_the_file_branch():
    block = {"type": "bookmark", "bookmark": {
        "url": "https://example.com/a", "caption": []}}
    assert extract.block_text(block) == "https://example.com/a"


def test_url_block_with_no_url_or_name():
    assert extract.block_text({"type": "embed", "embed": {"caption": rt("just a caption")}}) \
        == "just a caption"


def test_flatten_skips_empty_option_lists():
    """Present but empty multi_select / select must not become facets."""
    values, _ = extract.flatten_properties({
        "Tags": {"type": "multi_select", "multi_select": []},
        "Stage": {"type": "select", "select": {}},
    })
    assert values == {}


def test_flatten_ignores_title_in_the_text_blob_but_keeps_others():
    values, text = extract.flatten_properties({
        "Name": {"type": "title", "title": rt("Trip")},
        "Empty": {"type": "rich_text", "rich_text": []},
        "Note": {"type": "rich_text", "rich_text": rt("kept")},
    })
    assert "Trip" not in text and "Empty" not in text
    assert "Note: kept" in text
    assert values == {}
