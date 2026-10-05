"""Journal entries on trades (tags, notes, screenshots), on Postgres.

The port of ``tradejournal/journal.py`` (spike S2). Pure helpers and records come from the SQLite module; only the SQL
is rewritten. Differences from the original, each found while porting (doc 03 section 9.1):

- ``COLLATE NOCASE`` lookups became ``lower(...)``, including the plain ``=`` comparisons and the implicit orderings.
- ``GROUP BY tags.id`` also groups by the sort column from the joined table; Postgres insists on it.
- The ``HAVING`` alias is spelled out, and a color count is read by name (``dict()`` of two-key rows is silently wrong).
- ``remove_orphan_files`` is deliberately **not** ported: it compared a folder against *this user's* rows, which in a
  shared folder would delete every other user's files. Orphan cleanup becomes a system job over the whole object
  store (M6).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import psycopg

from ..journal import (
    TAG_COLOR_PRESETS,
    Screenshot,
    Tag,
    TagGroup,
    clean_color,
    clean_filename,
    clean_name,
    clean_notes,
    image_extension,
)
from . import usage


def tag_groups(conn: psycopg.Connection) -> list[TagGroup]:
    rows = conn.execute("SELECT id, name FROM tag_groups ORDER BY sort_order, lower(name)")
    return [TagGroup(r["id"], r["name"]) for r in rows]


def create_group(conn: psycopg.Connection, name: str) -> TagGroup:
    name = clean_name(name)
    if conn.execute("SELECT 1 FROM tag_groups WHERE lower(name) = lower(%s)", (name,)).fetchone():
        raise ValueError(f"there is already a category called {name!r}")
    next_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS next FROM tag_groups").fetchone()["next"]
    row = conn.execute(
        "INSERT INTO tag_groups (name, sort_order) VALUES (%s, %s) RETURNING id", (name, next_order)
    ).fetchone()
    return TagGroup(row["id"], name)


def rename_group(conn: psycopg.Connection, group_id: int, name: str) -> TagGroup:
    name = clean_name(name)
    if conn.execute("SELECT 1 FROM tag_groups WHERE lower(name) = lower(%s) AND id != %s", (name, group_id)).fetchone():
        raise ValueError(f"there is already a category called {name!r}")
    conn.execute("UPDATE tag_groups SET name = %s WHERE id = %s", (name, group_id))
    return TagGroup(group_id, name)


def delete_group(conn: psycopg.Connection, group_id: int) -> None:
    """Delete a group, its tags, and those tags' links to trades."""
    conn.execute("DELETE FROM tag_groups WHERE id = %s", (group_id,))


def _tag(row: dict, trade_count: int = 0) -> Tag:
    return Tag(row["id"], row["group_id"], row["name"], row["color"], trade_count)


def tags(conn: psycopg.Connection) -> list[Tag]:
    """Every tag with the number of trades carrying it, by group then name."""
    rows = conn.execute(
        """SELECT tags.id, tags.group_id, tags.name, tags.color, COUNT(trade_tags.trade_id) AS trade_count
           FROM tags
           JOIN tag_groups ON tag_groups.id = tags.group_id
           LEFT JOIN trade_tags ON trade_tags.tag_id = tags.id
           GROUP BY tags.id, tag_groups.sort_order
           ORDER BY tag_groups.sort_order, lower(tags.name)"""
    )
    return [_tag(r, r["trade_count"]) for r in rows]


def find_tag(conn: psycopg.Connection, group_id: int, name: str) -> Tag | None:
    """The group's tag with this name, ignoring capitalization and extra spaces."""
    row = conn.execute(
        "SELECT id, group_id, name, color FROM tags WHERE group_id = %s AND lower(name) = lower(%s)",
        (group_id, clean_name(name)),
    ).fetchone()
    return _tag(row) if row else None


def get_or_create_tag(conn: psycopg.Connection, group_id: int, name: str) -> Tag:
    """The group's tag with this name (any capitalization), creating it if needed.
    A new tag gets the preset color the fewest tags have."""
    name = clean_name(name)
    existing = find_tag(conn, group_id, name)
    if existing:
        return existing
    color = _next_color(conn)
    row = conn.execute(
        "INSERT INTO tags (group_id, name, color) VALUES (%s, %s, %s) RETURNING id", (group_id, name, color)
    ).fetchone()
    return Tag(row["id"], group_id, name, color)


def _next_color(conn: psycopg.Connection) -> str:
    in_use = {
        row["color"]: row["uses"] for row in conn.execute("SELECT color, COUNT(*) AS uses FROM tags GROUP BY color")
    }
    return min(TAG_COLOR_PRESETS, key=lambda color: in_use.get(color, 0))  # ties: first in preset order


def update_tag(conn: psycopg.Connection, tag_id: int, *, name: str, group_id: int, color: str) -> Tag:
    """Rename, recolor or move a tag to another group. The trades carrying it keep it.

    Raises ValueError if the group already has another tag with that name; merge_tags
    combines the two instead.
    """
    name, color = clean_name(name), clean_color(color)
    clash = find_tag(conn, group_id, name)
    if clash and clash.id != tag_id:
        raise ValueError(f"there is already a tag called {clash.name!r} in that category")
    conn.execute("UPDATE tags SET name = %s, group_id = %s, color = %s WHERE id = %s", (name, group_id, color, tag_id))
    return Tag(tag_id, group_id, name, color)


def merge_tags(conn: psycopg.Connection, tag_id: int, into_id: int) -> None:
    """Move every trade from one tag to another, then delete the first tag. A trade
    that carried both ends up with the remaining tag once."""
    if tag_id == into_id:
        raise ValueError("can't merge a tag into itself")
    conn.execute(
        "INSERT INTO trade_tags (trade_id, tag_id) SELECT trade_id, %s FROM trade_tags WHERE tag_id = %s "
        "ON CONFLICT DO NOTHING",
        (into_id, tag_id),
    )
    delete_tag(conn, tag_id)


def custom_colors(conn: psycopg.Connection) -> list[str]:
    """Colors on your tags that aren't presets, oldest first, so you can reuse them."""
    rows = conn.execute("SELECT color FROM tags GROUP BY color ORDER BY MIN(id)")
    return [row["color"] for row in rows if row["color"] not in TAG_COLOR_PRESETS]


def delete_tag(conn: psycopg.Connection, tag_id: int) -> None:
    """Delete a tag everywhere, including from the trades that carry it."""
    conn.execute("DELETE FROM tags WHERE id = %s", (tag_id,))


def trade_tags(conn: psycopg.Connection, trade_id: int) -> list[Tag]:
    rows = conn.execute(
        """SELECT tags.id, tags.group_id, tags.name, tags.color FROM trade_tags
           JOIN tags ON tags.id = trade_tags.tag_id
           JOIN tag_groups ON tag_groups.id = tags.group_id
           WHERE trade_tags.trade_id = %s
           ORDER BY tag_groups.sort_order, lower(tags.name)""",
        (trade_id,),
    )
    return [_tag(r) for r in rows]


def set_trade_tags(conn: psycopg.Connection, trade_id: int, group_id: int, names: Iterable[str]) -> list[Tag]:
    """Make the trade's tags in one group exactly these names; other groups are untouched.

    New names become tags. Names differing only in capitalization are the same tag.
    Returns the tags now on the trade in that group, with their stored spelling.
    """
    wanted: dict[int, Tag] = {}
    for name in names:
        tag = get_or_create_tag(conn, group_id, name)
        wanted.setdefault(tag.id, tag)
    conn.execute(
        "DELETE FROM trade_tags WHERE trade_id = %s AND tag_id IN (SELECT id FROM tags WHERE group_id = %s)",
        (trade_id, group_id),
    )
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO trade_tags (trade_id, tag_id) VALUES (%s, %s)", [(trade_id, tag_id) for tag_id in wanted]
        )
    return list(wanted.values())


def add_trade_tag(conn: psycopg.Connection, trade_id: int, tag_id: int) -> None:
    conn.execute("INSERT INTO trade_tags (trade_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (trade_id, tag_id))


def remove_trade_tag(conn: psycopg.Connection, trade_id: int, tag_id: int) -> None:
    """Take a tag off one trade. The tag itself stays available for other trades."""
    conn.execute("DELETE FROM trade_tags WHERE trade_id = %s AND tag_id = %s", (trade_id, tag_id))


def tags_by_trade(conn: psycopg.Connection) -> dict[int, list[Tag]]:
    """Tags of every tagged trade, ordered by group then name."""
    rows = conn.execute(
        """SELECT trade_tags.trade_id, tags.id, tags.group_id, tags.name, tags.color FROM trade_tags
           JOIN tags ON tags.id = trade_tags.tag_id
           JOIN tag_groups ON tag_groups.id = tags.group_id
           ORDER BY tag_groups.sort_order, lower(tags.name)"""
    )
    result: dict[int, list[Tag]] = {}
    for row in rows:
        result.setdefault(row["trade_id"], []).append(_tag(row))
    return result


# ---------------------------------------------------------------------------
# Notes


def notes(conn: psycopg.Connection, trade_id: int) -> str:
    row = conn.execute("SELECT notes FROM trades WHERE id = %s", (trade_id,)).fetchone()
    if row is None:
        raise LookupError(f"no trade with id {trade_id}")
    return row["notes"]


def set_notes(conn: psycopg.Connection, trade_id: int, html: str | None) -> None:
    conn.execute("UPDATE trades SET notes = %s WHERE id = %s", (clean_notes(html), trade_id))


def day_note(conn: psycopg.Connection, day: date) -> str:
    """The note for one trading day, or "" (a day with nothing written has no row)."""
    row = conn.execute("SELECT notes FROM day_notes WHERE trade_date = %s", (day,)).fetchone()
    return row["notes"] if row else ""


def set_day_note(conn: psycopg.Connection, day: date, html: str | None) -> None:
    """Replace the note for a trading day. A note with no visible text removes the row, so "which days have a note" stays
    exact. Any date is allowed: a day with no trades can have a note too."""
    text = clean_notes(html)
    if text == "":
        conn.execute("DELETE FROM day_notes WHERE trade_date = %s", (day,))
        return
    conn.execute(
        """INSERT INTO day_notes (trade_date, notes, updated_at) VALUES (%s, %s, now())
           ON CONFLICT (user_id, trade_date) DO UPDATE SET notes = EXCLUDED.notes, updated_at = now()""",
        (day, text),
    )


def days_with_notes(conn: psycopg.Connection, start: date, end: date) -> list[date]:
    """The trading days from `start` to `end` (both included) that have a note, oldest first."""
    rows = conn.execute(
        "SELECT trade_date FROM day_notes WHERE notes <> '' AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
        (start, end),
    )
    return [r["trade_date"] for r in rows]


# ---------------------------------------------------------------------------
# Screenshots (still files in a local folder; M6 moves them to object storage)


def _screenshot(row: dict) -> Screenshot:
    return Screenshot(
        row["id"], row["trade_id"], row["stored_name"], row["original_name"], row["created_at"].isoformat()
    )


def add_screenshot(
    conn: psycopg.Connection, folder: Path, trade_id: int, original_name: str, data: bytes
) -> Screenshot:
    """Save an image file for a trade under a random name and record it."""
    extension = image_extension(data)
    display_name = clean_filename(original_name)
    if conn.execute("SELECT 1 FROM trades WHERE id = %s", (trade_id,)).fetchone() is None:
        raise LookupError(f"no trade with id {trade_id}")
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    path = folder / stored_name
    path.write_bytes(data)
    try:
        row = conn.execute(
            "INSERT INTO screenshots (trade_id, stored_name, original_name) VALUES (%s, %s, %s) RETURNING *",
            (trade_id, stored_name, display_name),
        ).fetchone()
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    usage.refresh(conn)
    return _screenshot(row)


def screenshots(conn: psycopg.Connection, trade_id: int) -> list[Screenshot]:
    rows = conn.execute("SELECT * FROM screenshots WHERE trade_id = %s ORDER BY created_at, id", (trade_id,))
    return [_screenshot(r) for r in rows]


def delete_screenshot(conn: psycopg.Connection, screenshot_id: int) -> str | None:
    """Forget a screenshot. Returns its file name so the caller can delete the file
    once the transaction has committed (see remove_file)."""
    row = conn.execute("DELETE FROM screenshots WHERE id = %s RETURNING stored_name", (screenshot_id,)).fetchone()
    if row is None:
        return None
    usage.refresh(conn)
    return row["stored_name"]


def remove_file(folder: Path, stored_name: str) -> None:
    (folder / Path(stored_name).name).unlink(missing_ok=True)


def journal_summary(conn: psycopg.Connection) -> dict[int, tuple[bool, int]]:
    """For trades with any notes or screenshots: (has notes, number of screenshots)."""
    rows = conn.execute(
        """SELECT trades.id, trades.notes != '' AS has_notes, COUNT(screenshots.id) AS shots
           FROM trades LEFT JOIN screenshots ON screenshots.trade_id = trades.id
           GROUP BY trades.id
           HAVING trades.notes != '' OR COUNT(screenshots.id) > 0"""
    )
    return {row["id"]: (row["has_notes"], row["shots"]) for row in rows}
