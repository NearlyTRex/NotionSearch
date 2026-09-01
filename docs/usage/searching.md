# Searching

## Just type

Results update as you type, and you don't have to spell things correctly. Searching
`quarterly budgt` finds *Quarterly Budget*; `lisban` finds *Lisbon Trip*. Accents are
ignored too, so `cafe resume` finds *Café Résumé*.

You can also stop halfway through a word — `lisb` matches *Lisbon* — which is what
makes it usable when you only half-remember a title.

Press `/` anywhere on the page to jump to the search box.

## What gets searched

Everything the sync could read:

- Page and database titles
- The text of every block on the page — paragraphs, headings, lists, to-dos,
  toggles, quotes, callouts, code blocks, table rows, and captions on images and
  bookmarks
- Database row properties, such as Status, Tags, dates, and people
- The names of the pages a page lives inside

Titles are weighted above body text, so a page *called* "Budget" beats one that
merely mentions the word.

When two pages match equally well, the more recently edited one wins.

## Filters

Down the left side:

**Sort** — best match by default. Switch to recently edited, oldest first, or
alphabetical.

**Edited** — narrow to today, this week, this month, or this year. Useful when you
know you touched something recently but not what it was called.

**Type** — pages, databases, or everything.

**Location** — which part of Notion the page lives in, based on its parent page.
Good for "the meeting notes in Work, not the ones in Personal".

**Properties** — the Status, Tags, and similar values from your Notion databases.
Picking two values of the *same* property widens the search (Travel **or**
Finance); picking values of *different* properties narrows it (Planning **and**
Travel).

**Clear all filters** appears once any filter is active.

## Reading results

Each result shows the page's icon and title, the path to it, and a snippet with your
matched words highlighted.

Click any result to open a preview with the full text and properties, without
leaving the page. From there, **Open in Notion** jumps to the real page.

## An empty search box

Leaving the box empty lists your most recently edited pages, so the page is useful
before you've typed anything. Filters still apply, which makes "everything I edited
this week" a single click.

## When you can't find something

**It might not be synced.** Press **Sync** in the header — anything created since the
last sync won't be there yet.

**It might not be shared with the integration.** This is the most common cause.
Notion only exposes pages you've explicitly connected. See
[Getting started](getting-started.md#share-your-pages-with-it).

**Results look stale or wrong.** Settings → **Full rebuild** re-reads everything
from scratch.
