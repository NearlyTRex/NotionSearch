"""Turn Notion's block and property JSON into plain searchable text.

Search quality lives or dies here: anything this misses is invisible to the
user no matter how good the search engine is.
"""

from typing import Any

# Blocks whose text sits in a `rich_text` array under their own type key.
RICH_TEXT_BLOCKS = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "toggle",
    "quote",
    "callout",
    "code",
    "template",
    "table_of_contents",
}

# Blocks that carry a URL plus an optional caption.
URL_BLOCKS = {"bookmark", "embed", "link_preview", "image", "video", "file", "pdf", "audio"}


def rich_text(items: list[dict] | None) -> str:
    """Concatenate a Notion rich_text array into plain text."""
    if not items:
        return ""
    return "".join(item.get("plain_text", "") for item in items).strip()


def title_of(obj: dict) -> str:
    """Best-effort title for a page or database object."""
    if obj.get("object") == "database":
        text = rich_text(obj.get("title"))
        return text or "Untitled database"

    props = obj.get("properties") or {}
    for prop in props.values():
        if prop.get("type") == "title":
            text = rich_text(prop.get("title"))
            if text:
                return text
    return "Untitled"


def icon_of(obj: dict) -> str | None:
    """Return an emoji icon if the page has one (external images are skipped)."""
    icon = obj.get("icon") or {}
    if icon.get("type") == "emoji":
        return icon.get("emoji")
    return None


def block_text(block: dict) -> str:
    """Extract the visible text of a single block."""
    btype = block.get("type")
    if not btype:
        return ""
    body = block.get(btype) or {}

    if btype in RICH_TEXT_BLOCKS:
        text = rich_text(body.get("rich_text"))
        if btype == "to_do":
            mark = "[x]" if body.get("checked") else "[ ]"
            return f"{mark} {text}".strip()
        if btype == "code":
            lang = body.get("language") or ""
            caption = rich_text(body.get("caption"))
            return " ".join(p for p in (lang, text, caption) if p)
        if btype == "callout":
            icon = body.get("icon") or {}
            emoji = icon.get("emoji", "") if icon.get("type") == "emoji" else ""
            return f"{emoji} {text}".strip()
        return text

    if btype in URL_BLOCKS:
        parts = [rich_text(body.get("caption"))]
        url = body.get("url")
        if not url and body.get("type") == "external":
            url = (body.get("external") or {}).get("url")
        if not url and body.get("type") == "file":
            # Notion's own S3 links are signed and expire; the name is the useful part.
            url = (body.get("file") or {}).get("url", "").split("?")[0]
        if url:
            parts.append(url)
        if body.get("name"):
            parts.append(body["name"])
        return " ".join(p for p in parts if p).strip()

    if btype == "table_row":
        cells = body.get("cells") or []
        return " | ".join(rich_text(cell) for cell in cells).strip()

    if btype == "equation":
        return body.get("expression", "")

    if btype in ("child_page", "child_database"):
        return body.get("title", "")

    if btype == "link_to_page":
        return ""  # Reference only; the target page is indexed on its own.

    if btype in ("divider", "breadcrumb", "column", "column_list", "synced_block", "unsupported"):
        return ""

    # Unknown/new block type: try the generic shape rather than dropping it.
    return rich_text(body.get("rich_text"))


def property_value(prop: dict) -> str:
    """Render one page property as plain text."""
    ptype = prop.get("type")
    if not ptype:
        return ""
    value = prop.get(ptype)

    if ptype in ("title", "rich_text"):
        return rich_text(value)
    if ptype == "number":
        return "" if value is None else str(value)
    if ptype == "select":
        return (value or {}).get("name", "") if value else ""
    if ptype == "status":
        return (value or {}).get("name", "") if value else ""
    if ptype == "multi_select":
        return ", ".join(opt.get("name", "") for opt in (value or []))
    if ptype == "date":
        if not value:
            return ""
        start, end = value.get("start", ""), value.get("end")
        return f"{start} → {end}" if end else start
    if ptype == "people":
        return ", ".join(p.get("name", "") for p in (value or []) if p.get("name"))
    if ptype == "files":
        names = []
        for f in value or []:
            names.append(f.get("name", ""))
        return ", ".join(n for n in names if n)
    if ptype == "checkbox":
        return "Yes" if value else "No"
    if ptype in ("url", "email", "phone_number"):
        return value or ""
    if ptype == "formula":
        if not value:
            return ""
        inner = value.get("type")
        result = value.get(inner)
        if inner == "date" and isinstance(result, dict):
            return result.get("start", "")
        return "" if result is None else str(result)
    if ptype == "rollup":
        if not value:
            return ""
        inner = value.get("type")
        if inner == "array":
            return ", ".join(property_value(v) for v in value.get("array", []))
        result = value.get(inner)
        if inner == "date" and isinstance(result, dict):
            return result.get("start", "")
        return "" if result is None else str(result)
    if ptype in ("created_time", "last_edited_time"):
        return value or ""
    if ptype in ("created_by", "last_edited_by"):
        return (value or {}).get("name", "") if value else ""
    if ptype == "unique_id":
        if not value:
            return ""
        prefix = value.get("prefix") or ""
        return f"{prefix}-{value.get('number')}" if prefix else str(value.get("number"))
    if ptype == "relation":
        return ""  # IDs only; not useful as search text.
    return ""


def flatten_properties(properties: dict) -> tuple[dict[str, Any], str]:
    """Return (filterable values keyed by property name, searchable text blob).

    Only short, low-cardinality types become filters — those are the ones that
    make sense as facets in the UI.
    """
    values: dict[str, Any] = {}
    text_parts: list[str] = []

    for name, prop in (properties or {}).items():
        ptype = prop.get("type")
        rendered = property_value(prop)

        if ptype == "multi_select":
            options = [o.get("name") for o in (prop.get("multi_select") or []) if o.get("name")]
            if options:
                values[name] = options
        elif ptype in ("select", "status"):
            inner = prop.get(ptype) or {}
            if inner.get("name"):
                values[name] = inner["name"]
        elif ptype == "checkbox":
            values[name] = bool(prop.get("checkbox"))
        elif ptype == "people":
            names = [p.get("name") for p in (prop.get("people") or []) if p.get("name")]
            if names:
                values[name] = names

        if rendered and ptype != "title":
            text_parts.append(f"{name}: {rendered}")

    return values, "\n".join(text_parts)
