"""Journal entries on trades: notes, screenshots, and tags.

Tags live in editable groups (Setup, Mistake and Emotion to start), shown as
"categories" in the app; a tag's name is unique within its group, ignoring case, and
each tag has a color. Notes are rich text stored as HTML. Screenshots are image files
in the data folder, one database row each.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from .timeutil import to_utc_iso

MAX_NAME_LENGTH = 40
MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024

# Ready-made tag colors, in the order new tags take them. The first eight are the
# colors tags had before colors were editable (migration v6 keeps each tag's).
TAG_COLOR_PRESETS = {
    "#2a78d6": "Blue",
    "#6e5adc": "Violet",
    "#0ca30c": "Green",
    "#eb6834": "Orange",
    "#e87ba4": "Pink",
    "#1baf7a": "Aqua",
    "#d03b3b": "Red",
    "#eda100": "Amber",
    "#898781": "Gray",
}
_HEX_COLOR = re.compile(r"#?([0-9a-f]{3}|[0-9a-f]{6})")


@dataclass(frozen=True)
class TagGroup:
    id: int
    name: str


@dataclass(frozen=True)
class Tag:
    id: int
    group_id: int
    name: str
    color: str  # "#rrggbb"
    trade_count: int = 0  # filled in by tags(); 0 elsewhere


def clean_name(text: str) -> str:
    """Trim and collapse whitespace. Raises ValueError for empty or overly long names."""
    name = " ".join((text or "").split())
    if not name:
        raise ValueError("name is empty")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"keep names under {MAX_NAME_LENGTH} characters")
    return name


def clean_color(text: str) -> str:
    """A color as lowercase "#rrggbb" ("#ABC" becomes "#aabbcc"). Raises ValueError."""
    match = _HEX_COLOR.fullmatch((text or "").strip().lower())
    if not match:
        raise ValueError(f"not a hex color like #2a78d6: {text!r}")
    digits = match.group(1)
    return "#" + (digits if len(digits) == 6 else "".join(c * 2 for c in digits))


def tag_groups(conn: sqlite3.Connection) -> list[TagGroup]:
    rows = conn.execute("SELECT id, name FROM tag_groups ORDER BY sort_order, name")
    return [TagGroup(r["id"], r["name"]) for r in rows]


def create_group(conn: sqlite3.Connection, name: str) -> TagGroup:
    name = clean_name(name)
    if conn.execute("SELECT 1 FROM tag_groups WHERE name = ?", (name,)).fetchone():
        raise ValueError(f"there is already a category called {name!r}")
    next_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tag_groups").fetchone()[0]
    cursor = conn.execute("INSERT INTO tag_groups (name, sort_order) VALUES (?, ?)", (name, next_order))
    return TagGroup(cursor.lastrowid, name)


def rename_group(conn: sqlite3.Connection, group_id: int, name: str) -> TagGroup:
    name = clean_name(name)
    if conn.execute("SELECT 1 FROM tag_groups WHERE name = ? AND id != ?", (name, group_id)).fetchone():
        raise ValueError(f"there is already a category called {name!r}")
    conn.execute("UPDATE tag_groups SET name = ? WHERE id = ?", (name, group_id))
    return TagGroup(group_id, name)


def delete_group(conn: sqlite3.Connection, group_id: int) -> None:
    """Delete a group, its tags, and those tags' links to trades."""
    conn.execute("DELETE FROM tag_groups WHERE id = ?", (group_id,))


def _tag(row: sqlite3.Row, trade_count: int = 0) -> Tag:
    return Tag(row["id"], row["group_id"], row["name"], row["color"], trade_count)


def tags(conn: sqlite3.Connection) -> list[Tag]:
    """Every tag with the number of trades carrying it, by group then name."""
    rows = conn.execute(
        """SELECT tags.id, tags.group_id, tags.name, tags.color, COUNT(trade_tags.trade_id) AS trade_count
           FROM tags
           JOIN tag_groups ON tag_groups.id = tags.group_id
           LEFT JOIN trade_tags ON trade_tags.tag_id = tags.id
           GROUP BY tags.id
           ORDER BY tag_groups.sort_order, tags.name COLLATE NOCASE"""
    )
    return [_tag(r, r["trade_count"]) for r in rows]


def find_tag(conn: sqlite3.Connection, group_id: int, name: str) -> Tag | None:
    """The group's tag with this name, ignoring capitalization and extra spaces."""
    row = conn.execute(
        "SELECT id, group_id, name, color FROM tags WHERE group_id = ? AND name = ?", (group_id, clean_name(name))
    ).fetchone()
    return _tag(row) if row else None


def get_or_create_tag(conn: sqlite3.Connection, group_id: int, name: str) -> Tag:
    """The group's tag with this name (any capitalization), creating it if needed.
    A new tag gets the preset color the fewest tags have."""
    name = clean_name(name)
    existing = find_tag(conn, group_id, name)
    if existing:
        return existing
    color = _next_color(conn)
    cursor = conn.execute("INSERT INTO tags (group_id, name, color) VALUES (?, ?, ?)", (group_id, name, color))
    return Tag(cursor.lastrowid, group_id, name, color)


def _next_color(conn: sqlite3.Connection) -> str:
    in_use = dict(conn.execute("SELECT color, COUNT(*) FROM tags GROUP BY color").fetchall())
    return min(TAG_COLOR_PRESETS, key=lambda color: in_use.get(color, 0))  # ties: first in preset order


def update_tag(conn: sqlite3.Connection, tag_id: int, *, name: str, group_id: int, color: str) -> Tag:
    """Rename, recolor or move a tag to another group. The trades carrying it keep it.

    Raises ValueError if the group already has another tag with that name; merge_tags
    combines the two instead.
    """
    name, color = clean_name(name), clean_color(color)
    clash = find_tag(conn, group_id, name)
    if clash and clash.id != tag_id:
        raise ValueError(f"there is already a tag called {clash.name!r} in that category")
    conn.execute("UPDATE tags SET name = ?, group_id = ?, color = ? WHERE id = ?", (name, group_id, color, tag_id))
    return Tag(tag_id, group_id, name, color)


def merge_tags(conn: sqlite3.Connection, tag_id: int, into_id: int) -> None:
    """Move every trade from one tag to another, then delete the first tag. A trade
    that carried both ends up with the remaining tag once."""
    if tag_id == into_id:
        raise ValueError("can't merge a tag into itself")
    conn.execute(
        "INSERT OR IGNORE INTO trade_tags (trade_id, tag_id) SELECT trade_id, ? FROM trade_tags WHERE tag_id = ?",
        (into_id, tag_id),
    )
    delete_tag(conn, tag_id)


def custom_colors(conn: sqlite3.Connection) -> list[str]:
    """Colors on your tags that aren't presets, oldest first, so you can reuse them."""
    rows = conn.execute("SELECT color FROM tags GROUP BY color ORDER BY MIN(id)")
    return [row["color"] for row in rows if row["color"] not in TAG_COLOR_PRESETS]


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> None:
    """Delete a tag everywhere, including from the trades that carry it."""
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))


def trade_tags(conn: sqlite3.Connection, trade_id: int) -> list[Tag]:
    rows = conn.execute(
        """SELECT tags.id, tags.group_id, tags.name, tags.color FROM trade_tags
           JOIN tags ON tags.id = trade_tags.tag_id
           JOIN tag_groups ON tag_groups.id = tags.group_id
           WHERE trade_tags.trade_id = ?
           ORDER BY tag_groups.sort_order, tags.name COLLATE NOCASE""",
        (trade_id,),
    )
    return [_tag(r) for r in rows]


def set_trade_tags(conn: sqlite3.Connection, trade_id: int, group_id: int, names: Iterable[str]) -> list[Tag]:
    """Make the trade's tags in one group exactly these names; other groups are untouched.

    New names become tags. Names differing only in capitalization are the same tag.
    Returns the tags now on the trade in that group, with their stored spelling.
    """
    wanted: dict[int, Tag] = {}
    for name in names:
        tag = get_or_create_tag(conn, group_id, name)
        wanted.setdefault(tag.id, tag)
    conn.execute(
        "DELETE FROM trade_tags WHERE trade_id = ? AND tag_id IN (SELECT id FROM tags WHERE group_id = ?)",
        (trade_id, group_id),
    )
    conn.executemany(
        "INSERT INTO trade_tags (trade_id, tag_id) VALUES (?, ?)", [(trade_id, tag_id) for tag_id in wanted]
    )
    return list(wanted.values())


def add_trade_tag(conn: sqlite3.Connection, trade_id: int, tag_id: int) -> None:
    conn.execute("INSERT OR IGNORE INTO trade_tags (trade_id, tag_id) VALUES (?, ?)", (trade_id, tag_id))


def remove_trade_tag(conn: sqlite3.Connection, trade_id: int, tag_id: int) -> None:
    """Take a tag off one trade. The tag itself stays available for other trades."""
    conn.execute("DELETE FROM trade_tags WHERE trade_id = ? AND tag_id = ?", (trade_id, tag_id))


def tags_by_trade(conn: sqlite3.Connection) -> dict[int, list[Tag]]:
    """Tags of every tagged trade, ordered by group then name."""
    rows = conn.execute(
        """SELECT trade_tags.trade_id, tags.id, tags.group_id, tags.name, tags.color FROM trade_tags
           JOIN tags ON tags.id = trade_tags.tag_id
           JOIN tag_groups ON tag_groups.id = tags.group_id
           ORDER BY tag_groups.sort_order, tags.name COLLATE NOCASE"""
    )
    result: dict[int, list[Tag]] = {}
    for row in rows:
        result.setdefault(row["trade_id"], []).append(_tag(row))
    return result


# ---------------------------------------------------------------------------
# Notes

_TAG_OR_SPACE = re.compile(r"<[^>]*>|&nbsp;|\s")


def clean_notes(html: str | None) -> str:
    """Store "" for notes with no visible text (an emptied editor leaves "<br>" behind)."""
    html = (html or "").strip()
    return html if _TAG_OR_SPACE.sub("", html) else ""


def notes(conn: sqlite3.Connection, trade_id: int) -> str:
    row = conn.execute("SELECT notes FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if row is None:
        raise LookupError(f"no trade with id {trade_id}")
    return row["notes"]


def set_notes(conn: sqlite3.Connection, trade_id: int, html: str | None) -> None:
    conn.execute("UPDATE trades SET notes = ? WHERE id = ?", (clean_notes(html), trade_id))


# ---------------------------------------------------------------------------
# Screenshots

# File signatures of the image types accepted, and the extension each is stored with.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)


@dataclass(frozen=True)
class Screenshot:
    id: int
    trade_id: int
    stored_name: str  # file name inside the screenshots folder
    original_name: str
    created_at: str


def image_extension(data: bytes) -> str:
    """The file extension for PNG, JPEG, GIF or WebP data. Raises ValueError otherwise.

    Checks the bytes themselves, not the file name, so a renamed file can't sneak in.
    """
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise ValueError(f"images must be under {MAX_SCREENSHOT_BYTES // (1024 * 1024)} MB")
    for signature, extension in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return extension
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("not a PNG, JPEG, GIF or WebP image")


def clean_filename(name: str | None) -> str:
    """The file's own name with any folders removed, for display.

    Uploads can come from Windows machines (C:\\charts\\a.png) or Unix ones (/home/me/a.png),
    and the server may be either. Path would only split on the server's own separator, so a
    Windows path on a Linux server would keep its folder names. PureWindowsPath splits on both.
    """
    return PureWindowsPath(name or "").name or "screenshot"


def add_screenshot(
    conn: sqlite3.Connection, folder: Path, trade_id: int, original_name: str, data: bytes
) -> Screenshot:
    """Save an image file for a trade under a random name and record it."""
    extension = image_extension(data)
    display_name = clean_filename(original_name)
    if conn.execute("SELECT 1 FROM trades WHERE id = ?", (trade_id,)).fetchone() is None:
        raise LookupError(f"no trade with id {trade_id}")
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    path = folder / stored_name
    path.write_bytes(data)
    try:
        created_at = to_utc_iso(datetime.now(UTC))
        cursor = conn.execute(
            "INSERT INTO screenshots (trade_id, stored_name, original_name, created_at) VALUES (?, ?, ?, ?)",
            (trade_id, stored_name, display_name, created_at),
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return Screenshot(cursor.lastrowid, trade_id, stored_name, display_name, created_at)


def screenshots(conn: sqlite3.Connection, trade_id: int) -> list[Screenshot]:
    rows = conn.execute("SELECT * FROM screenshots WHERE trade_id = ? ORDER BY created_at, id", (trade_id,))
    return [Screenshot(r["id"], r["trade_id"], r["stored_name"], r["original_name"], r["created_at"]) for r in rows]


def delete_screenshot(conn: sqlite3.Connection, screenshot_id: int) -> str | None:
    """Forget a screenshot. Returns its file name so the caller can delete the file
    once the transaction has committed (see remove_file)."""
    row = conn.execute("SELECT stored_name FROM screenshots WHERE id = ?", (screenshot_id,)).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM screenshots WHERE id = ?", (screenshot_id,))
    return row["stored_name"]


def remove_file(folder: Path, stored_name: str) -> None:
    (folder / Path(stored_name).name).unlink(missing_ok=True)


def remove_orphan_files(conn: sqlite3.Connection, folder: Path) -> int:
    """Delete image files no screenshot row points to (left behind when an import undo
    removed their trade, or an interrupted save). Returns how many were removed."""
    if not folder.is_dir():
        return 0
    known = {row["stored_name"] for row in conn.execute("SELECT stored_name FROM screenshots")}
    removed = 0
    for path in folder.iterdir():
        if path.is_file() and path.name not in known:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def journal_summary(conn: sqlite3.Connection) -> dict[int, tuple[bool, int]]:
    """For trades with any notes or screenshots: (has notes, number of screenshots)."""
    rows = conn.execute(
        """SELECT trades.id, trades.notes != '' AS has_notes, COUNT(screenshots.id) AS shots
           FROM trades LEFT JOIN screenshots ON screenshots.trade_id = trades.id
           GROUP BY trades.id
           HAVING has_notes OR shots > 0"""
    )
    return {row["id"]: (bool(row["has_notes"]), row["shots"]) for row in rows}
