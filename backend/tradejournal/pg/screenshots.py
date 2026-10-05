"""Screenshots of a trade: a row in the database and the bytes in the file store (doc 03 section 6).

Every function takes a connection from ``Database.tenant_session``, so row-level security limits it to one person's rows
and another person's id is simply not found. The order of events keeps the two halves from disagreeing:

- **add** checks everything first (the image, the room), writes the file, then inserts the row; if the insert fails the
  file is removed again. A crash between the two leaves a file with no row, which the orphan job reports.
- **delete** removes the row inside the transaction and hands back the file's key; the caller removes the file only after
  the transaction has committed, because a rolled-back delete must not have lost the file.

The limits here are the interim ones: per file, per trade and per person, in constants. What a plan includes is the
owner's decision (``entitlements.LIMITS`` is empty on purpose); when it is made, the per-person figure moves there.
"""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from .. import images
from ..filestore import FileStore
from ..journal import clean_filename
from . import store as pg_store
from . import usage

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PER_TRADE = 20
MAX_STORED_BYTES = 500 * 1024 * 1024  # per person, until plans decide it
MAX_NAME_LENGTH = 200


@dataclass(frozen=True)
class Shot:
    id: int
    trade_id: int
    key: str  # where the bytes are in the store (the `stored_name` column)
    original_name: str
    created_at: datetime
    content_type: str
    size_bytes: int
    width: int | None
    height: int | None


def _shot(row: dict) -> Shot:
    return Shot(
        id=row["id"],
        trade_id=row["trade_id"],
        key=row["stored_name"],
        original_name=row["original_name"],
        created_at=row["created_at"],
        # A file from before sizes were recorded has no type on record: it is known by the end of its name.
        content_type=row["content_type"] or images.content_type_of(row["stored_name"]),
        size_bytes=row["size_bytes"],
        width=row["width"],
        height=row["height"],
    )


def add(
    conn: psycopg.Connection, files: FileStore, user_id: uuid.UUID, trade_id: int, original_name: str, data: bytes
) -> Shot:
    """Check an image, store it for a trade and record it. `LookupError`: no such trade (or not this person's).
    `ValueError` (with a sentence for the person): too big, not an image, too many for the trade, no room left."""
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Images must be under {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    info = images.inspect(data)
    pg_store.lock_user(conn)  # two uploads at once must not both slip under the same limit
    if conn.execute("SELECT 1 FROM trades WHERE id = %s", (trade_id,)).fetchone() is None:
        raise LookupError(f"no trade with id {trade_id}")
    on_trade = conn.execute("SELECT count(*) AS n FROM screenshots WHERE trade_id = %s", (trade_id,)).fetchone()["n"]
    if on_trade >= MAX_PER_TRADE:
        raise ValueError(f"A trade can have {MAX_PER_TRADE} screenshots at most. Delete one to add another.")
    held = conn.execute("SELECT coalesce(sum(size_bytes), 0) AS n FROM screenshots").fetchone()["n"]
    if held + len(data) > MAX_STORED_BYTES:
        raise ValueError(
            f"That would take you over your {MAX_STORED_BYTES // (1024 * 1024)} MB of screenshot space. "
            "Delete some you no longer need to make room."
        )
    key = f"u/{user_id}/{uuid.uuid4().hex}{info.extension}"
    files.put(key, data)
    try:
        row = conn.execute(
            """INSERT INTO screenshots (trade_id, stored_name, original_name, content_type, size_bytes, width, height)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (
                trade_id,
                key,
                clean_filename(original_name)[:MAX_NAME_LENGTH] or f"screenshot{info.extension}",
                info.content_type,
                len(data),
                info.width,
                info.height,
            ),
        ).fetchone()
        usage.refresh(conn)
    except BaseException:  # the row goes with the transaction, so the file must go too
        # A store that is down cannot be tidied now: the orphan report finds the file.
        with contextlib.suppress(OSError):
            files.delete(key)
        raise
    return _shot(row)


def get(conn: psycopg.Connection, screenshot_id: int) -> Shot | None:
    row = conn.execute("SELECT * FROM screenshots WHERE id = %s", (screenshot_id,)).fetchone()
    return _shot(row) if row else None


def for_trade(conn: psycopg.Connection, trade_id: int) -> list[Shot]:
    rows = conn.execute("SELECT * FROM screenshots WHERE trade_id = %s ORDER BY created_at, id", (trade_id,))
    return [_shot(r) for r in rows]


def delete(conn: psycopg.Connection, screenshot_id: int) -> Shot | None:
    """Forget a screenshot. Returns it so the caller can remove its file once the transaction has committed."""
    row = conn.execute("DELETE FROM screenshots WHERE id = %s RETURNING *", (screenshot_id,)).fetchone()
    if row is None:
        return None
    usage.refresh(conn)
    return _shot(row)
